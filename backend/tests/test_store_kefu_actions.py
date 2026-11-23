"""客服写操作（preview → confirm → reply）的事务性测试，全部打桩不联网。"""
import pytest
from fastapi.testclient import TestClient

from app.database import create_user, get_kefu_action, upsert_temu_session
from app.main import app
from app.services import store_kefu, store_kefu_actions, store_session, system, temu_bridge
from app.services.auth import hash_password
from app.services.store_session import StoreError
from app.services.temu_bridge import BridgeError

ACCOUNT = "action-demo@example.com"
SKC = "418931287377"

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
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture()
def bridge(monkeypatch):
    state = {
        "writes_enabled": True,
        "intercept_code": 0,
        "intercept_msg": "",
        "product": {
            "ok": True,
            "code": "1000000",
            "found": True,
            "product_name": "8件渔网网纱镂空绑带连体衣套装",
            "product_picture": "https://img.example.com/skc.jpg",
            "product_skc_id": SKC,
            "product_id": "49589818098",
        },
        "submit_calls": [],
        "submit_result": {"ok": True, "code": "1000000", "msgId": 638869614600999},
        "replies": [{"contentType": 12, "msgId": 638869614601000, "content": "该商品已下架"}],
    }

    monkeypatch.setattr(store_kefu, "tools", lambda user_id, force=False: [dict(TOOL)])
    monkeypatch.setattr(store_kefu, "query_product", lambda *args, **kwargs: dict(state["product"]))
    monkeypatch.setattr(
        store_kefu,
        "card",
        lambda user_id, **kwargs: {
            "msg_id": "638869614600280",
            "tool_id": "2406230000031",
            "data_type": "SKC",
            "option_title": "下架原因",
            "options": TOOL["options"],
        },
    )
    # create_action 走 ensure_card（带卡片新鲜度判断）
    monkeypatch.setattr(
        store_kefu,
        "ensure_card",
        lambda user_id, **kwargs: {
            "msg_id": "638869614600280",
            "tool_id": "2406230000031",
            "data_type": "SKC",
            "option_title": "下架原因",
            "options": TOOL["options"],
            "source": "find",
        },
    )
    monkeypatch.setattr(
        temu_bridge,
        "kefu_intercept",
        lambda account, data_id, tool_id=None: {
            "ok": True,
            "interceptCode": state["intercept_code"],
            "interceptMsg": state["intercept_msg"],
            "passed": state["intercept_code"] == 0,
        },
    )

    def fake_submit(account, payload, *, dry_run=True):
        state["submit_calls"].append({"payload": payload, "dry_run": dry_run})
        return dict(state["submit_result"])

    monkeypatch.setattr(temu_bridge, "kefu_submit", fake_submit)
    monkeypatch.setattr(temu_bridge, "kefu_reply", lambda account, since_msg_id=None: {"ok": True, "replies": state["replies"]})
    monkeypatch.setattr(system, "get", lambda: {"kefu_writes_enabled": state["writes_enabled"]})
    # 提交间隔保护在测试里关掉（否则同一账号的第二次提交会 429）
    monkeypatch.setattr(store_kefu_actions, "MIN_SUBMIT_INTERVAL", 0)
    return state


def _create(client: TestClient, **overrides):
    payload = {
        "tool_id": TOOL["tool_id"],
        "data_type": "SKC",
        "data_id": SKC,
        "reason_code": 2001,
    }
    payload.update(overrides)
    return client.post("/api/store/kefu/actions", json=payload)


def test_global_kill_switch_blocks_writes_but_allows_preview_data(bridge):
    client = _client("action_kill", "pass-action-0")
    try:
        bridge["writes_enabled"] = False
        response = _create(client)
        assert response.status_code == 403
        assert "只读" in response.json()["detail"]
        assert bridge["submit_calls"] == []
    finally:
        client.__exit__(None, None, None)


def test_data_type_must_match_tool(bridge):
    client = _client("action_type", "pass-action-1")
    try:
        response = _create(client, data_type="SKU")
        assert response.status_code == 400
        assert "要求 SKC" in response.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_reason_is_required_and_other_needs_detail(bridge):
    client = _client("action_reason", "pass-action-2")
    try:
        missing = _create(client, reason_code=None)
        assert missing.status_code == 400
        assert "请选择" in missing.json()["detail"]

        other_without_detail = _create(client, reason_code=2008)
        assert other_without_detail.status_code == 400
        assert "具体原因" in other_without_detail.json()["detail"]

        invalid = _create(client, reason_code=9999)
        assert invalid.status_code == 400
    finally:
        client.__exit__(None, None, None)


def test_incomplete_product_is_rejected_before_intercept(bridge):
    client = _client("action_product", "pass-action-3")
    try:
        bridge["product"] = {"ok": True, "product_name": "", "product_picture": ""}
        response = _create(client)
        assert response.status_code == 409
        assert "查不到" in response.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_intercepted_action_is_recorded_as_failed(bridge):
    client = _client("action_intercept", "pass-action-4")
    try:
        bridge["intercept_code"] = 1
        bridge["intercept_msg"] = {"hint": "该商品上次咨询结果正在返回中", "subHint": "请勿重复咨询"}
        response = _create(client)
        assert response.status_code == 409
        assert "拦截检查未通过" in response.json()["detail"]

        items = client.get("/api/store/kefu/actions").json()["items"]
        assert items[0]["stage"] == "failed"
        assert "请勿重复咨询" in items[0]["error"]
    finally:
        client.__exit__(None, None, None)


def test_preview_persists_body_and_confirm_sends_once(bridge):
    client = _client("action_flow", "pass-action-5")
    try:
        created = _create(client)
        assert created.status_code == 201, created.text
        action = created.json()
        assert action["stage"] == "preview"
        assert action["parent_msg_id"] == "638869614600280"
        assert action["preview"]["dataId"] == SKC
        assert action["preview"]["optionInfo"] == {"code": 2001, "desc": "业务调整下架", "detailedReason": None}
        assert action["request_body"]["contentType"] == 7
        assert action["request_body"]["parentMsgId"] == 638869614600280
        assert "8件渔网" in action["product_snapshot"]["product_name"]

        confirmed = client.post(f"/api/store/kefu/actions/{action['id']}/confirm")
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["stage"] == "sent"
        assert confirmed.json()["msg_id"] == "638869614600999"

        call = bridge["submit_calls"][0]
        assert call["dry_run"] is False
        # 关键：复用创建时已消费的商品与拦截结果，避免二次拦截
        assert call["payload"]["intercept"] == {"ok": True, "interceptCode": 0, "passed": True}
        assert call["payload"]["product"]["productName"].startswith("8件渔网")
        assert call["payload"]["skcId"] == SKC
        assert call["payload"]["parentMsgId"] == "638869614600280"

        # 幂等：再次确认不再发消息
        again = client.post(f"/api/store/kefu/actions/{action['id']}/confirm")
        assert again.status_code == 200
        assert len(bridge["submit_calls"]) == 1
    finally:
        client.__exit__(None, None, None)


def test_reply_marks_action_replied(bridge):
    client = _client("action_reply", "pass-action-6")
    try:
        action = _create(client).json()
        client.post(f"/api/store/kefu/actions/{action['id']}/confirm")

        reply = client.post(f"/api/store/kefu/actions/{action['id']}/reply")
        assert reply.status_code == 200, reply.text
        data = reply.json()
        assert data["stage"] == "replied"
        assert data["robot_reply"]["content"] == "该商品已下架"
        assert data["finished_at"]

        # 已终结的记录再轮询直接返回，不再打上游
        assert client.post(f"/api/store/kefu/actions/{action['id']}/reply").json()["stage"] == "replied"
    finally:
        client.__exit__(None, None, None)


def test_other_users_action_is_invisible(bridge):
    owner = _client("action_owner", "pass-action-7")
    action_id = ""
    try:
        action_id = _create(owner).json()["id"]
    finally:
        owner.__exit__(None, None, None)

    other = _client("action_other", "pass-action-8")
    try:
        assert other.get(f"/api/store/kefu/actions/{action_id}").status_code == 404
        assert other.post(f"/api/store/kefu/actions/{action_id}/confirm").status_code == 404
        assert other.get("/api/store/kefu/actions").json()["items"] == []
    finally:
        other.__exit__(None, None, None)


def test_submit_failure_marks_action_failed(bridge):
    client = _client("action_fail", "pass-action-9")
    try:
        action = _create(client).json()
        bridge["submit_result"] = {"ok": False, "code": "40002", "err": "Message content is incomplete"}
        response = client.post(f"/api/store/kefu/actions/{action['id']}/confirm")
        assert response.status_code == 502
        stored = get_kefu_action(action["id"])
        assert stored["stage"] == "failed"
        assert "incomplete" in stored["error"]
    finally:
        client.__exit__(None, None, None)



def test_degraded_message_is_queued_for_fast_retry(bridge, monkeypatch):
    """「已进入加速等待序列」是降级信号（未进同步执行器），要转 retry_pending 快速重试。"""
    monkeypatch.setattr(
        temu_bridge,
        "kefu_messages",
        lambda account, limit=50: {"items": []},
    )
    monkeypatch.setattr(
        temu_bridge,
        "kefu_reply",
        lambda account, since_msg_id=None: {
            "ok": True,
            "replies": [
                {
                    "contentType": 1,
                    "msgId": 639451773884032,
                    "content": "您好~ 您的提交已进入加速等待序列，请稍候\n处理好后会在官方客服给您回复",
                }
            ],
        },
    )

    client = _client("action_degraded", "pass-degraded-1")
    try:
        action = _create(client).json()
        client.post(f"/api/store/kefu/actions/{action['id']}/confirm")
        reply = client.post(f"/api/store/kefu/actions/{action['id']}/reply")
        assert reply.status_code == 200, reply.text
        data = reply.json()
        assert data["stage"] == "retry_pending", "降级不能被当成已受理"
        assert data["retry_count"] == 1
        assert "降级" in data["error"]
        assert data["next_retry_at"], "降级应排队快速重试"
    finally:
        client.__exit__(None, None, None)


def test_classify_reply_distinguishes_degraded_and_pending():
    """降级（加速等待）与真正受理（请稍候/正在处理）必须分开。"""
    cases = [
        ({"contentType": 12, "content": "<p>该商品已下架</p>"}, "replied"),
        ({"contentType": 1, "content": "您好~ 您的提交已进入加速等待序列，请稍候"}, "degraded"),
        ({"contentType": 1, "content": "正在处理中，请稍候"}, "accepted"),
        ({"contentType": 1, "content": "非常抱歉操作失败，系统忙碌中，请您稍后重试"}, "failed"),
        ({"contentType": 1, "content": "该商品还未发布到任何站点，暂时无法操作下架"}, "rejected"),
        ({"contentType": 1, "content": "随便什么话"}, "unknown"),
    ]
    for message, expected in cases:
        assert store_kefu_actions.classify_reply(message) == expected, message["content"]


def test_failed_reply_queues_retry_with_backoff(bridge, monkeypatch):
    monkeypatch.setattr(
        temu_bridge,
        "kefu_reply",
        lambda account, since_msg_id=None: {
            "ok": True,
            "replies": [{"contentType": 1, "msgId": 639451790000001, "content": "非常抱歉操作失败，系统忙碌中，请您稍后重试"}],
        },
    )
    monkeypatch.setattr(temu_bridge, "kefu_messages", lambda account, limit=50: {"items": []})

    client = _client("action_retry", "pass-retry-1")
    try:
        action = _create(client).json()
        client.post(f"/api/store/kefu/actions/{action['id']}/confirm")
        data = client.post(f"/api/store/kefu/actions/{action['id']}/reply").json()
        assert data["stage"] == "retry_pending", data
        assert data["retry_count"] == 1
        assert data["next_retry_at"]
        assert "已排队重试" in data["error"]
    finally:
        client.__exit__(None, None, None)


def test_rejected_reply_is_terminal_without_retry(bridge, monkeypatch):
    monkeypatch.setattr(
        temu_bridge,
        "kefu_reply",
        lambda account, since_msg_id=None: {
            "ok": True,
            "replies": [{"contentType": 1, "msgId": 639451790000002, "content": "该商品还未发布到任何站点，暂时无法操作下架"}],
        },
    )
    monkeypatch.setattr(temu_bridge, "kefu_messages", lambda account, limit=50: {"items": []})

    client = _client("action_rejected", "pass-rejected-1")
    try:
        action = _create(client).json()
        client.post(f"/api/store/kefu/actions/{action['id']}/confirm")
        data = client.post(f"/api/store/kefu/actions/{action['id']}/reply").json()
        assert data["stage"] == "rejected"
        assert data["retry_count"] == 0
        assert "还未发布到任何站点" in data["error"]
    finally:
        client.__exit__(None, None, None)


def test_retry_scheduler_respects_switch(bridge, monkeypatch):
    from app.services import store_kefu_retry

    monkeypatch.setattr(system, "get", lambda: {"kefu_auto_retry_enabled": False, "kefu_writes_enabled": True})
    assert store_kefu_retry.run_once() == 0

    monkeypatch.setattr(system, "get", lambda: {"kefu_auto_retry_enabled": True, "kefu_writes_enabled": True})
    # 没有到期的 retry_pending 记录
    assert store_kefu_retry.run_once() == 0


def test_card_missing_falls_back_to_previous_parent(bridge, monkeypatch):
    """卡片被挤出最近 50 条时，复用历史成功提交用过的 parentMsgId。"""
    from datetime import datetime, timezone

    from app.database import create_kefu_action as db_create_kefu_action

    client = _client("action_card_fallback", "pass-card-1")
    try:
        me = client.get("/api/auth/me").json()
        db_create_kefu_action(
            "hist-parent-1",
            user_id=me["id"],
            account_masked=store_session.mask_account(ACCOUNT),
            tool_id=TOOL["tool_id"],
            tool_name=TOOL["name"],
            data_type=1,
            data_type_name="SKC",
            data_id="111",
            stage="sent",
            parent_msg_id="639404416750487",
            sent_at=datetime.now(timezone.utc),
        )
        monkeypatch.setattr(
            store_kefu,
            "ensure_card",
            lambda user_id, **kwargs: (_ for _ in ()).throw(StoreError("未找到自助工具卡片", code="CARD_EMPTY", status=404)),
        )

        response = _create(client)
        assert response.status_code == 201, response.text
        assert response.json()["parent_msg_id"] == "639404416750487"
    finally:
        client.__exit__(None, None, None)


def test_card_missing_without_history_raises_hint(bridge, monkeypatch):
    client = _client("action_card_missing", "pass-card-2")
    try:
        monkeypatch.setattr(
            store_kefu,
            "ensure_card",
            lambda user_id, **kwargs: (_ for _ in ()).throw(StoreError("未找到自助工具卡片", code="CARD_EMPTY", status=404)),
        )
        # 屏蔽历史回退，验证「真的没有任何可用 parentMsgId」时的提示
        monkeypatch.setattr(store_kefu_actions, "latest_kefu_parent_msg_id", lambda *args, **kwargs: "")

        response = _create(client)
        assert response.status_code == 409
        assert "联系官方客服" in response.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_admin_can_list_all_actions(bridge):
    user_client = _client("action_user_admin", "pass-action-10")
    try:
        _create(user_client)
    finally:
        user_client.__exit__(None, None, None)

    admin = create_user("action_admin", hash_password("pass-action-11"), role="admin", status="approved")
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": admin["username"], "password": "pass-action-11"}).status_code == 200
        items = client.get("/api/admin/kefu-actions").json()["items"]
        assert any(item["tool_name"] == "商品下架" for item in items)
