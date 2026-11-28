"""P4 后台页面接口：店铺会话 / 登录日志 / 客服操作审计。"""
from fastapi.testclient import TestClient

from app.database import add_temu_login_log, create_user, upsert_temu_session
from app.main import app
from app.services import store_session
from app.services.auth import hash_password


def _admin_client(username: str, password: str) -> TestClient:
    create_user(username, hash_password(password), role="admin", status="approved")
    client = TestClient(app)
    client.__enter__()
    assert client.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200
    return client


def test_store_logs_are_admin_only():
    create_user("p4_normal", hash_password("pass-p4-normal"), status="approved", store_access=True, categories=["A"])
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "p4_normal", "password": "pass-p4-normal"}).status_code == 200
        assert client.get("/api/admin/store-logs").status_code == 403
        assert client.get("/api/admin/store-sessions").status_code == 403
        assert client.get("/api/admin/kefu-actions").status_code == 403


def test_admin_sees_login_logs_with_actions():
    add_temu_login_log(None, "p4***@example.com", "login_failed", "40002001", "账号或密码不正确", "127.0.0.1")
    admin = _admin_client("p4_admin_logs", "pass-p4-logs")
    try:
        response = admin.get("/api/admin/store-logs", params={"action": "login_failed", "limit": 10})
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert items, "应当能查到刚才写入的登录失败日志"
        assert items[0]["action"] == "login_failed"
        assert items[0]["code"] == "40002001"
        assert items[0]["account_masked"] == "p4***@example.com"
    finally:
        admin.__exit__(None, None, None)


def test_admin_session_overview_and_forced_logout():
    user = create_user("p4_session_user", hash_password("pass-p4-session"), status="approved", store_access=True, categories=["A"])
    upsert_temu_session(
        user["id"],
        account="p4-session@example.com",
        account_hash=store_session.account_hash("p4-session@example.com"),
        account_masked=store_session.mask_account("p4-session@example.com"),
        mall_id="100001",
        mall_name="P4 演示店铺",
        status="active",
    )
    admin = _admin_client("p4_admin_sessions", "pass-p4-sessions")
    try:
        listing = admin.get("/api/admin/store-sessions")
        assert listing.status_code == 200, listing.text
        row = next(item for item in listing.json()["items"] if item["user_id"] == user["id"])
        assert row["mall_name"] == "P4 演示店铺"
        assert row["username"] == "p4_session_user"
        assert row["store_access"] is True
        # 账号必须脱敏，不能回明文
        assert row["account_masked"] == "p***@example.com"

        revoked = admin.delete(f"/api/admin/store-sessions/{user['id']}")
        assert revoked.status_code == 200
        assert revoked.json()["revoked"] is True

        after = admin.get("/api/admin/store-sessions").json()["items"]
        assert all(item["mall_name"] != "P4 演示店铺" for item in after)
    finally:
        admin.__exit__(None, None, None)


def test_admin_kefu_actions_carry_operator_info():
    from app.database import create_kefu_action

    owner = create_user("p4_action_owner", hash_password("pass-p4-owner"), display_name="运营小张", status="approved", store_access=True, categories=["A"])
    create_kefu_action(
        "p4-action-1",
        user_id=owner["id"],
        tool_id="2406230000031",
        tool_name="商品下架",
        data_type=1,
        data_type_name="SKC",
        data_id="418931287377",
        stage="preview",
    )
    admin = _admin_client("p4_admin_actions", "pass-p4-actions")
    try:
        response = admin.get("/api/admin/kefu-actions", params={"limit": 20})
        assert response.status_code == 200, response.text
        row = next(item for item in response.json()["items"] if item["id"] == "p4-action-1")
        assert row["username"] == "p4_action_owner"
        assert row["display_name"] == "运营小张"
        assert row["tool_name"] == "商品下架"
    finally:
        admin.__exit__(None, None, None)
