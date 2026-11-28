"""客服自助工具（只读）的行为测试，bridge 全部打桩。"""
import pytest
from fastapi.testclient import TestClient

from app.database import create_user, upsert_temu_session
from app.main import app
from app.services import store_kefu, store_session, temu_bridge
from app.services.auth import hash_password
from app.services.temu_bridge import BridgeError

ACCOUNT = "kefu-demo@example.com"

DOWNGRADE_REASONS = [
    {"code": 2000, "desc": "亏损下架"},
    {"code": 2001, "desc": "业务调整下架"},
    {"code": 2002, "desc": "经营/库存策略下架"},
    {"code": 2003, "desc": "节假日原因下架"},
    {"code": 2004, "desc": "物流问题下架"},
    {"code": 2005, "desc": "测款失败下架"},
    {"code": 2006, "desc": "清仓品下架"},
    {"code": 2007, "desc": "换季品下架"},
    {"code": 2008, "desc": "其他", "needInput": True},
]


def _client(username: str, password: str, *, with_session: bool = True) -> TestClient:
    user = create_user(username, hash_password(password), status="approved", store_access=True, categories=["A"])
    if with_session:
        upsert_temu_session(
            user["id"],
            account=ACCOUNT,
            account_hash=store_session.account_hash(ACCOUNT),
            account_masked=store_session.mask_account(ACCOUNT),
            mall_id="100001",
            mall_name="演示店铺",
            status="active",
        )
    client = TestClient(app)
    client.__enter__()
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture()
def bridge(monkeypatch):
    store_kefu.invalidate_tools_cache()
    state = {
        "tools_calls": 0,
        "messages_calls": [],
        "card_fail": None,
        "query_result": {
            "ok": True,
            "code": "1000000",
            "productName": "8件渔网网纱镂空绑带连体衣套装",
            "productPicture": "https://img.example.com/skc.jpg",
            "productSkcId": 418931287377,
            "productId": 49589818098,
            "algoProductId": None,
            "isSemi": True,
            "isCloth": False,
            "hasBondedOrAbroadStock": False,
        },
    }

    def fake_tools(account):
        state["tools_calls"] += 1
        return {
            "total": 4,
            "tools": [
                {
                    "toolName": "商品发布到站点",
                    "toolId": "2406230000016",
                    "dataIdType": 1,
                    "ableUse": True,
                    "onlyFullyHosted": False,
                    "optionItemList": None,
                    "rateLimitMs": 5000,
                    "successTip": "已提交",
                    "failTip": "失败",
                    "raw": {"huge": "payload"},
                },
                {
                    "toolName": "商品下架",
                    "toolId": "2406230000031",
                    "dataIdType": 1,
                    "ableUse": True,
                    "optionTitle": "下架原因",
                    "optionItemList": DOWNGRADE_REASONS,
                    "rateLimitMs": 5000,
                    "raw": {"huge": "payload"},
                },
                {
                    "toolName": "价格刷新",
                    "toolId": "2406280000031",
                    "dataIdType": 2,
                    "ableUse": True,
                    "rateLimitMs": 3000,
                },
                {
                    "toolName": "商品站点/信息异常查询",
                    "toolId": "2508210000009",
                    "dataIdType": 5,
                    "ableUse": False,
                },
            ],
        }

    def fake_card(account, *, tool_id=None, max_pages=10):
        if state["card_fail"]:
            raise state["card_fail"]
        return {
            "msgId": 638869614600280,
            "toolId": "2406230000031",
            "dataType": 1,
            "dataTypeList": [1],
            "optionTitle": "下架原因",
            "optionItemList": DOWNGRADE_REASONS,
            "popUpItem": {"title": "发送下架商品信息", "confirmBtnText": "申请下架"},
            "card": {"toolId": "2406230000031"},
        }

    def fake_messages(account, *, limit=50):
        state["messages_calls"].append(limit)
        return {
            "total": 3,
            "items": [
                {"msgId": 1, "contentType": 6, "timestamp": 1761000000000, "content": '{"toolId":"2406230000031"}'},
                {"msgId": 2, "contentType": 7, "timestamp": 1761000060000, "content": "已提交"},
                {"msgId": 3, "contentType": 12, "timestamp": 1761000120000, "content": "该商品已下架"},
            ],
        }

    def fake_query(account, data_type, data_id, tool_id=None):
        return dict(state["query_result"])

    monkeypatch.setattr(temu_bridge, "kefu_tools", fake_tools)
    monkeypatch.setattr(temu_bridge, "kefu_card", fake_card)
    monkeypatch.setattr(temu_bridge, "kefu_messages", fake_messages)
    monkeypatch.setattr(temu_bridge, "kefu_query", fake_query)
    return state


def test_kefu_requires_logged_in_store(bridge):
    client = _client("kefu_no_store", "pass-kefu-0", with_session=False)
    try:
        assert client.get("/api/store/kefu/tools").status_code == 401
        assert client.get("/api/store/kefu/card").status_code == 401
        assert client.get("/api/store/kefu/messages").status_code == 401
        assert client.post("/api/store/kefu/query", json={"type": "SKC", "id": "1"}).status_code == 401
    finally:
        client.__exit__(None, None, None)


def test_tools_are_trimmed_and_cached(bridge):
    client = _client("kefu_tools_user", "pass-kefu-1")
    try:
        response = client.get("/api/store/kefu/tools")
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert len(items) == 4
        assert items[0]["name"] == "商品发布到站点"
        assert items[0]["data_type"] == "SKC"
        assert items[0]["rate_limit_ms"] == 5000
        assert "raw" not in items[0], "上游 raw 字段不下发"
        # 下架原因：2008 需要填具体原因
        downgrade = items[1]
        assert downgrade["option_title"] == "下架原因"
        assert len(downgrade["options"]) == 9
        assert downgrade["options"][-1] == {"code": 2008, "desc": "其他", "need_input": True}
        # 不可用的工具如实透出
        assert items[3]["enabled"] is False

        client.get("/api/store/kefu/tools")
        assert bridge["tools_calls"] == 1, "30 分钟内应命中缓存"
        client.get("/api/store/kefu/tools", params={"force": "true"})
        assert bridge["tools_calls"] == 2
    finally:
        client.__exit__(None, None, None)


def test_card_returns_enum_and_popup(bridge):
    client = _client("kefu_card_user", "pass-kefu-2")
    try:
        response = client.get("/api/store/kefu/card")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["msg_id"] == "638869614600280"
        assert data["tool_id"] == "2406230000031"
        assert data["data_type"] == "SKC"
        assert len(data["options"]) == 9
        assert data["popup"]["confirmBtnText"] == "申请下架"
        assert "card" in data
    finally:
        client.__exit__(None, None, None)


def test_missing_card_maps_to_404_with_onboarding_hint(bridge):
    client = _client("kefu_card_user2", "pass-kefu-3")
    try:
        bridge["card_fail"] = BridgeError("EMPTY", "未找到自助工具卡片（contentType=6）", stage="kefu.card", status=500)
        response = client.get("/api/store/kefu/card")
        assert response.status_code == 404
        assert "联系官方客服" in response.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_messages_limit_is_clamped_to_fifty(bridge):
    client = _client("kefu_msg_user", "pass-kefu-4")
    try:
        response = client.get("/api/store/kefu/messages", params={"limit": 200})
        # 超出上限由 FastAPI 校验拦下（le=50）
        assert response.status_code == 422

        response = client.get("/api/store/kefu/messages", params={"limit": 30})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["limit"] == 30
        assert data["items"][0]["content_type_name"] == "自助工具卡"
        assert data["items"][1]["content_type_name"] == "工具提交"
        assert data["items"][2]["content_type_name"] == "机器人回执"
        assert bridge["messages_calls"] == [30]
    finally:
        client.__exit__(None, None, None)


def test_query_product_validates_type_and_id(bridge):
    client = _client("kefu_query_user", "pass-kefu-5")
    try:
        assert client.post("/api/store/kefu/query", json={"type": "ORDER", "id": "1"}).status_code == 400
        bad_id = client.post("/api/store/kefu/query", json={"type": "SKC", "id": "abc"})
        assert bad_id.status_code == 400
        assert "纯数字" in bad_id.json()["detail"]
        empty_id = client.post("/api/store/kefu/query", json={"type": "SKC", "id": "  "})
        assert empty_id.status_code == 400
    finally:
        client.__exit__(None, None, None)


def test_query_product_returns_identity_for_confirmation(bridge):
    client = _client("kefu_query_user2", "pass-kefu-6")
    try:
        response = client.post("/api/store/kefu/query", json={"type": "skc", "id": "418931287377"})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["found"] is True
        assert data["product_name"].startswith("8件渔网")
        assert data["product_picture"].endswith("skc.jpg")
        assert data["product_skc_id"] == "418931287377"
        assert data["ok"] is True
    finally:
        client.__exit__(None, None, None)
