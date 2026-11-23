"""店铺管理模块准入（users.store_access）的接口级测试。"""
from fastapi.testclient import TestClient

from app.database import create_user, get_temu_session, upsert_temu_session
from app.main import app
from app.services import store_session, temu_bridge
from app.services.auth import hash_password


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text


def test_store_module_is_hidden_until_admin_grants_access():
    admin = create_user("store_access_admin", hash_password("pass-admin-1"), role="admin", status="approved")
    user = create_user("store_access_user", hash_password("pass-user-1"), status="approved")
    assert user["store_access"] is False

    with TestClient(app) as client:
        _login(client, "store_access_user", "pass-user-1")
        me = client.get("/api/auth/me").json()
        assert me["store_access"] is False
        blocked = client.get("/api/store/session")
        assert blocked.status_code == 403
        assert "店铺管理" in blocked.json()["detail"]

        client.post("/api/auth/logout")
        _login(client, "store_access_admin", "pass-admin-1")
        granted = client.patch(f"/api/admin/users/{user['id']}", json={"store_access": True})
        assert granted.status_code == 200, granted.text
        assert granted.json()["store_access"] is True

        client.post("/api/auth/logout")
        _login(client, "store_access_user", "pass-user-1")
        assert client.get("/api/auth/me").json()["store_access"] is True
        allowed = client.get("/api/store/session")
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["status"] == "logged_out"
        assert allowed.json()["bridge_configured"] is False


def test_admin_always_has_store_access_even_if_flag_is_false():
    admin = create_user("store_access_admin2", hash_password("pass-admin-2"), role="admin", status="approved")
    with TestClient(app) as client:
        _login(client, "store_access_admin2", "pass-admin-2")
        # 前端依赖 me.store_access 渲染入口；管理员恒为 True
        assert client.get("/api/auth/me").json()["store_access"] is True
        assert client.get("/api/store/session").status_code == 200
        # 管理员账号不能被当作普通用户编辑
        assert client.patch(f"/api/admin/users/{admin['id']}", json={"store_access": False}).status_code == 404


def test_revoking_access_forces_store_logout_and_clears_credentials(monkeypatch):
    admin = create_user("store_access_admin3", hash_password("pass-admin-3"), role="admin", status="approved")
    user = create_user("store_access_user3", hash_password("pass-user-3"), status="approved", store_access=True)

    # 伪造一个活跃会话，验证关闭权限时会强制退出
    upsert_temu_session(
        user["id"],
        account="demo1@example.com",
        account_hash=store_session.account_hash("demo1@example.com"),
        account_masked=store_session.mask_account("demo1@example.com"),
        mall_id="100001",
        mall_name="演示店铺",
        status="active",
        remember=True,
    )
    calls = []
    monkeypatch.setattr(temu_bridge, "configured", lambda: True)
    monkeypatch.setattr(temu_bridge, "logout", lambda account: calls.append(account) or {"loggedOut": True})

    with TestClient(app) as client:
        _login(client, "store_access_admin3", "pass-admin-3")
        response = client.patch(f"/api/admin/users/{user['id']}", json={"store_access": False})
        assert response.status_code == 200, response.text
        assert response.json()["store_access"] is False

    assert calls == ["demo1@example.com"], "关闭权限必须调用 bridge 登出"
    session = get_temu_session(user["id"])
    assert session["status"] == "logged_out"
    assert session["account"] == ""
    assert session["mall_name"] == ""


def test_bulk_grant_and_revoke_reports_revoked_sessions(monkeypatch):
    admin = create_user("store_access_admin4", hash_password("pass-admin-4"), role="admin", status="approved")
    first = create_user("store_access_bulk1", hash_password("pass-bulk-1"), status="approved")
    second = create_user("store_access_bulk2", hash_password("pass-bulk-2"), status="approved", store_access=True)

    for user in (first, second):
        upsert_temu_session(
            user["id"],
            account=f"bulk{user['id']}@example.com",
            account_hash=store_session.account_hash(f"bulk{user['id']}@example.com"),
            account_masked=store_session.mask_account(f"bulk{user['id']}@example.com"),
            status="active",
            mall_name="批量店铺",
        )

    rented = []
    monkeypatch.setattr(temu_bridge, "configured", lambda: True)
    monkeypatch.setattr(temu_bridge, "logout", lambda account: rented.append(account) or {"loggedOut": True})

    with TestClient(app) as client:
        _login(client, "store_access_admin4", "pass-admin-4")
        granted = client.patch(
            "/api/admin/users/bulk",
            json={"user_ids": [first["id"], second["id"]], "store_access": True},
        )
        assert granted.status_code == 200, granted.text
        assert {item["store_access"] for item in granted.json()["items"]} == {True}
        assert granted.json()["store_sessions_revoked"] == 0

        revoked = client.patch(
            "/api/admin/users/bulk",
            json={"user_ids": [first["id"], second["id"]], "store_access": False},
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["store_sessions_revoked"] == 2

    assert sorted(rented) == sorted([f"bulk{first['id']}@example.com", f"bulk{second['id']}@example.com"])
    for user in (first, second):
        assert get_temu_session(user["id"])["status"] == "logged_out"


def test_bulk_update_requires_at_least_one_field():
    admin = create_user("store_access_admin5", hash_password("pass-admin-5"), role="admin", status="approved")
    target = create_user("store_access_user5", hash_password("pass-user-5"), status="approved")
    with TestClient(app) as client:
        _login(client, "store_access_admin5", "pass-admin-5")
        response = client.patch("/api/admin/users/bulk", json={"user_ids": [target["id"]]})
        assert response.status_code == 400
