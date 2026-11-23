"""客服工具批量执行的测试（不联网，线程改为同步执行）。"""
import pytest
from fastapi.testclient import TestClient

from app.database import create_user, get_kefu_batch, list_kefu_actions_by_batch, upsert_temu_session
from app.main import app
from app.services import store_kefu, store_kefu_actions, store_kefu_batch, store_session, system, temu_bridge
from app.services.auth import hash_password

ACCOUNT = "batch-demo@example.com"

TOOL = {
    "name": "商品下架",
    "tool_id": "2406230000031",
    "data_id_type": 1,
    "data_type": "SKC",
    "enabled": True,
    "only_fully_hosted": False,
    "option_title": "下架原因",
    "options": [
        {"code": 2001, "desc": "业务调整下架", "need_input": False},
        {"code": 2008, "desc": "其他", "need_input": True},
    ],
    "rate_limit_ms": 5000,
    "success_tip": "已提交",
    "fail_tip": "失败",
}


class _SyncThread:
    """把后台线程换成同步执行，测试里就不需要 sleep + 轮询。"""

    def __init__(self, target=None, args=(), kwargs=None, name=None, daemon=None):
        self._target = target
        self._args = args or ()
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


def _run_sync(monkeypatch):
    """只替换 batch 模块自己的线程工厂，不碰全局 threading。"""

    def fake_spawn(target, args, name):
        target(*args)

    monkeypatch.setattr(store_kefu_batch, "_spawn", fake_spawn)


def _client(username: str, password: str) -> TestClient:
    user = create_user(username, hash_password(password), status="approved", store_access=True)
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
    assert client.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200
    return client


@pytest.fixture()
def bridge(monkeypatch):
    state = {
        "writes_enabled": True,
        "intercept_map": {},          # data_id -> interceptCode
        "submit_fail": set(),         # data_id 提交失败
        "unknown": set(),             # 查不到商品的编号
        "intercept_calls": 0,
        "submit_calls": [],
    }

    monkeypatch.setattr(store_kefu, "tools", lambda user_id, force=False: [dict(TOOL)])
    monkeypatch.setattr(
        store_kefu,
        "card",
        lambda user_id, **kwargs: {"msg_id": "638869614600280", "tool_id": TOOL["tool_id"], "options": TOOL["options"]},
    )
    monkeypatch.setattr(
        store_kefu,
        "ensure_card",
        lambda user_id, **kwargs: {
            "msg_id": "638869614600280",
            "tool_id": TOOL["tool_id"],
            "options": TOOL["options"],
            "source": "find",
        },
    )

    def fake_query(user_id, data_type, data_id, tool_id=None):
        if data_id in state["unknown"]:
            return {"ok": False, "found": False, "product_name": "", "product_picture": "", "message": "查不到"}
        return {
            "ok": True,
            "code": "1000000",
            "found": True,
            "product_name": f"商品 {data_id}",
            "product_picture": f"https://img.example.com/{data_id}.jpg",
            "product_skc_id": data_id,
            "product_id": f"9{data_id}",
        }

    monkeypatch.setattr(store_kefu, "query_product", fake_query)

    def fake_kefu_query(account, data_type, data_id, tool_id=None):
        """批量只读查询走的是 bridge 客户端，这里单独打桩。"""
        if data_id in state["unknown"]:
            return {"ok": False, "code": "3000000", "msg": "查不到商品"}
        return {
            "ok": True,
            "code": "1000000",
            "productName": f"商品 {data_id}",
            "productPicture": f"https://img.example.com/{data_id}.jpg",
            "productSkcId": data_id,
            "productId": f"9{data_id}",
        }

    monkeypatch.setattr(temu_bridge, "kefu_query", fake_kefu_query)

    def fake_intercept(account, data_id, tool_id=None):
        state["intercept_calls"] += 1
        code = state["intercept_map"].get(data_id, 0)
        return {
            "ok": True,
            "interceptCode": code,
            "interceptMsg": "该商品上次咨询结果正在返回中" if code else "",
            "passed": code == 0,
        }

    monkeypatch.setattr(temu_bridge, "kefu_intercept", fake_intercept)

    def fake_submit(account, payload, *, dry_run=True):
        data_id = payload.get("skcId") or payload.get("skuId") or payload.get("spuId")
        state["submit_calls"].append({"data_id": data_id, "dry_run": dry_run})
        if data_id in state["submit_fail"]:
            return {"ok": False, "code": "40002", "err": "提交被拒"}
        return {"ok": True, "code": "1000000", "msgId": f"999{data_id}"}

    monkeypatch.setattr(temu_bridge, "kefu_submit", fake_submit)
    monkeypatch.setattr(temu_bridge, "kefu_reply", lambda account, since_msg_id=None: {"ok": True, "replies": []})
    monkeypatch.setattr(system, "get", lambda: {"kefu_writes_enabled": state["writes_enabled"]})
    monkeypatch.setattr(store_kefu_actions, "MIN_SUBMIT_INTERVAL", 0)
    _run_sync(monkeypatch)
    monkeypatch.setattr(store_kefu_batch, "ITEM_DELAY_SECONDS", 0)
    monkeypatch.setattr(store_kefu_batch, "REPLY_INLINE_WAIT", 0)
    monkeypatch.setattr(store_kefu_batch, "REPLY_DELAY_SECONDS", 0)
    monkeypatch.setattr(store_kefu_batch, "REPLY_ROUNDS", 1)
    return state


def test_normalize_ids_handles_mixed_separators():
    raw = "418931287377\n418931287377, 418931287380 418931287381；abc\n\t"
    assert store_kefu.normalize_ids(raw) == ["418931287377", "418931287380", "418931287381"]
    assert store_kefu.normalize_ids("") == []
    assert len(store_kefu.normalize_ids("\n".join(str(1000 + i) for i in range(80)))) == store_kefu.MAX_BATCH_IDS


def test_query_batch_returns_rows_and_keeps_going_on_error(bridge):
    client = _client("batch_query", "pass-batch-1")
    try:
        bridge["unknown"].add("2002")
        response = client.post("/api/store/kefu/query-batch", json={"type": "SKC", "ids": "2001\n2002\n2001"})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["total"] == 2, "应去重"
        assert data["items"][0]["product_name"] == "商品 2001"
        assert data["items"][1]["found"] is False
        assert data["items"][1]["data_id"] == "2002"
    finally:
        client.__exit__(None, None, None)


def test_batch_runs_every_id_and_records_results(bridge):
    client = _client("batch_run", "pass-batch-2")
    try:
        created = client.post(
            "/api/store/kefu/batches",
            json={"tool_id": TOOL["tool_id"], "ids": "3001\n3002\n3003", "reason_code": 2001},
        )
        assert created.status_code == 202, created.text
        batch_id = created.json()["batch"]["id"]

        view = client.get(f"/api/store/kefu/batches/{batch_id}").json()
        assert view["batch"]["status"] == "done"
        assert view["batch"]["total"] == 3
        assert view["batch"]["succeeded"] == 3
        assert view["progress"]["processed"] == 3
        assert [item["data_id"] for item in view["items"]] == ["3001", "3002", "3003"]
        assert all(item["stage"] == "sent" for item in view["items"])
        assert len(bridge["submit_calls"]) == 3
        assert all(call["dry_run"] is False for call in bridge["submit_calls"])
        # 拦截检查每个编号只调用一次
        assert bridge["intercept_calls"] == 3
    finally:
        client.__exit__(None, None, None)


def test_intercepted_and_failed_items_are_reported_per_row(bridge):
    client = _client("batch_mixed", "pass-batch-3")
    try:
        bridge["intercept_map"] = {"4002": 1}
        bridge["unknown"].add("4003")
        bridge["submit_fail"].add("4004")

        created = client.post(
            "/api/store/kefu/batches",
            json={"tool_id": TOOL["tool_id"], "ids": "4001\n4002\n4003\n4004", "reason_code": 2001},
        )
        assert created.status_code == 202, created.text
        batch_id = created.json()["batch"]["id"]
        view = client.get(f"/api/store/kefu/batches/{batch_id}").json()

        rows = {item["data_id"]: item for item in view["items"]}
        assert rows["4001"]["stage"] == "sent"
        assert rows["4002"]["stage"] == "failed"
        assert "咨询结果正在返回中" in rows["4002"]["error"]
        assert rows["4003"]["stage"] == "failed"
        assert "查不到" in rows["4003"]["error"]
        assert rows["4004"]["stage"] == "failed"
        assert "提交被拒" in rows["4004"]["error"]
        assert view["batch"]["succeeded"] == 1
        assert view["batch"]["failed"] == 3
    finally:
        client.__exit__(None, None, None)


def test_batch_requires_reason_and_ids(bridge):
    client = _client("batch_validate", "pass-batch-4")
    try:
        no_ids = client.post("/api/store/kefu/batches", json={"tool_id": TOOL["tool_id"], "ids": "abc,def", "reason_code": 2001})
        assert no_ids.status_code == 400
        assert "纯数字" in no_ids.json()["detail"]

        no_reason = client.post("/api/store/kefu/batches", json={"tool_id": TOOL["tool_id"], "ids": "5001"})
        assert no_reason.status_code == 400

        other_without_detail = client.post(
            "/api/store/kefu/batches",
            json={"tool_id": TOOL["tool_id"], "ids": "5001", "reason_code": 2008},
        )
        assert other_without_detail.status_code == 400
        assert "具体原因" in other_without_detail.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_batch_blocked_by_kill_switch(bridge):
    client = _client("batch_kill", "pass-batch-5")
    try:
        bridge["writes_enabled"] = False
        response = client.post("/api/store/kefu/batches", json={"tool_id": TOOL["tool_id"], "ids": "6001", "reason_code": 2001})
        assert response.status_code == 403
        assert bridge["submit_calls"] == []
    finally:
        client.__exit__(None, None, None)


def test_cancel_marks_batch_and_keeps_sent_items(bridge):
    client = _client("batch_cancel", "pass-batch-6")
    try:
        created = client.post(
            "/api/store/kefu/batches",
            json={"tool_id": TOOL["tool_id"], "ids": "7001\n7002", "reason_code": 2001},
        )
        batch_id = created.json()["batch"]["id"]
        # 已完成的任务取消是幂等的
        cancelled = client.post(f"/api/store/kefu/batches/{batch_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["batch"]["status"] == "done"

        stored = get_kefu_batch(batch_id)
        assert stored["succeeded"] == 2
        assert len(list_kefu_actions_by_batch(batch_id)) == 2
    finally:
        client.__exit__(None, None, None)


def test_batch_is_scoped_to_owner(bridge):
    owner = _client("batch_owner", "pass-batch-7")
    batch_id = ""
    try:
        batch_id = owner.post(
            "/api/store/kefu/batches",
            json={"tool_id": TOOL["tool_id"], "ids": "8001", "reason_code": 2001},
        ).json()["batch"]["id"]
    finally:
        owner.__exit__(None, None, None)

    other = _client("batch_other", "pass-batch-8")
    try:
        assert other.get(f"/api/store/kefu/batches/{batch_id}").status_code == 404
        assert other.post(f"/api/store/kefu/batches/{batch_id}/cancel").status_code == 404
    finally:
        other.__exit__(None, None, None)


def test_batch_appears_in_action_audit(bridge):
    client = _client("batch_audit", "pass-batch-9")
    try:
        batch_id = client.post(
            "/api/store/kefu/batches",
            json={"tool_id": TOOL["tool_id"], "ids": "9001", "reason_code": 2001},
        ).json()["batch"]["id"]
        items = client.get("/api/store/kefu/actions").json()["items"]
        assert any(item["batch_id"] == batch_id for item in items)
    finally:
        client.__exit__(None, None, None)


def test_reply_falls_back_to_time_window(bridge, monkeypatch):
    """实测坑：机器人回执的 msgId 可能小于提交 msgId，严格过滤会漏掉。

    兜底逻辑按「发送时间之后的第一条机器人消息」认领；但认领到的是平台固定话术，
    所以状态只推进到「已受理」，不能当成执行结果。
    """
    import time

    monkeypatch.setattr(temu_bridge, "kefu_reply", lambda account, since_msg_id=None: {"ok": True, "replies": []})
    monkeypatch.setattr(
        temu_bridge,
        "kefu_messages",
        lambda account, limit=50: {
            "items": [
                {
                    "msg_id": "639451773884032",
                    "content_type": 1,
                    "timestamp": int(time.time() * 1000) + 5000,
                    "content": "您好~ 您的提交已进入加速等待序列，请稍候",
                }
            ]
        },
    )

    client = _client("batch_reply_window", "pass-batch-10")
    try:
        batch_id = client.post(
            "/api/store/kefu/batches",
            json={"tool_id": TOOL["tool_id"], "ids": "1101", "reason_code": 2001},
        ).json()["batch"]["id"]
        view = client.get(f"/api/store/kefu/batches/{batch_id}").json()
        # 「加速等待」= 降级信号 → 不进 accepted，直接排队快速重试
        assert view["items"][0]["stage"] == "retry_pending"
        assert "降级" in view["items"][0]["error"]
        assert view["progress"]["awaiting_reply"] == 0
    finally:
        client.__exit__(None, None, None)


def test_terminal_reply_marks_item_replied(bridge, monkeypatch):
    """contentType=12 才是平台的终态结果。"""
    monkeypatch.setattr(
        temu_bridge,
        "kefu_reply",
        lambda account, since_msg_id=None: {
            "ok": True,
            "replies": [{"contentType": 12, "msgId": 639451780000001, "content": "该商品已下架"}],
        },
    )

    client = _client("batch_reply_terminal", "pass-batch-12")
    try:
        batch_id = client.post(
            "/api/store/kefu/batches",
            json={"tool_id": TOOL["tool_id"], "ids": "1301", "reason_code": 2001},
        ).json()["batch"]["id"]
        view = client.get(f"/api/store/kefu/batches/{batch_id}").json()
        assert view["items"][0]["stage"] == "replied"
        assert "已下架" in (view["items"][0]["robot_reply"] or {}).get("content", "")
        assert view["progress"]["awaiting_reply"] == 0
    finally:
        client.__exit__(None, None, None)


def test_unreplied_items_stay_sent_and_are_counted(bridge, monkeypatch):
    """回执一直不来时保持「已提交」，并在进度里给出等待回执的数量。"""
    monkeypatch.setattr(temu_bridge, "kefu_reply", lambda account, since_msg_id=None: {"ok": True, "replies": []})
    monkeypatch.setattr(temu_bridge, "kefu_messages", lambda account, limit=50: {"items": []})

    client = _client("batch_reply_wait", "pass-batch-11")
    try:
        batch_id = client.post(
            "/api/store/kefu/batches",
            json={"tool_id": TOOL["tool_id"], "ids": "1201\n1202", "reason_code": 2001},
        ).json()["batch"]["id"]
        view = client.get(f"/api/store/kefu/batches/{batch_id}").json()
        assert all(item["stage"] == "sent" for item in view["items"])
        assert view["progress"]["awaiting_reply"] == 2
    finally:
        client.__exit__(None, None, None)
