"""店铺会话（登录/退出/记住密码/账号占用）的行为测试。

bridge 全部用 monkeypatch 打桩：这些用例不联网、不碰真实 Temu 账号。
"""
import base64
import os

import pytest
from fastapi.testclient import TestClient

from app.database import (
    create_user,
    get_temu_credential,
    get_temu_session,
    upsert_temu_session,
)
from app.main import app
from app.services import credential_cipher, store_session, temu_bridge
from app.services.auth import hash_password

KEY = base64.b64encode(os.urandom(32)).decode()


@pytest.fixture()
def bridge(monkeypatch):
    """打桩 bridge：记录调用顺序，可按用例改返回值。"""
    state = {"login_result": {"mallId": "100001", "mallName": "演示店铺", "mallList": [{"mallId": "100001", "mallName": "演示店铺"}], "alive": True}, "calls": [], "fail": None}

    monkeypatch.setattr(temu_bridge, "configured", lambda: True)

    def fake_login(account, password, mall_id=None, verify_code=None):
        state["calls"].append(("login", account, password))
        if state["fail"]:
            raise state["fail"]
        return dict(state["login_result"])

    def fake_logout(account):
        state["calls"].append(("logout", account))
        return {"loggedOut": True}

    monkeypatch.setattr(temu_bridge, "login", fake_login)
    monkeypatch.setattr(temu_bridge, "logout", fake_logout)
    monkeypatch.setattr(temu_bridge, "health", lambda account: {"alive": True, "mallName": "演示店铺", "mallList": []})
    return state


def _client_for(username: str, password: str, *, store_access: bool = True) -> TestClient:
    create_user(username, hash_password(password), status="approved", store_access=store_access)
    client = TestClient(app)
    client.__enter__()
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def _login_store(client: TestClient, account: str, password: str, remember: bool = False) -> dict:
    return client.post(
        "/api/store/session/login",
        json={"account": account, "password": password, "remember": remember},
    )


def test_login_creates_active_session_and_never_stores_password(bridge):
    client = _client_for("store_login_a", "pass-login-a")
    try:
        response = _login_store(client, "demo2@example.com", "temu-secret-a")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "active"
        assert data["mall_name"] == "演示店铺"
        assert data["account_masked"] == "d***@example.com"
        assert data["remember"] is False
        assert data["bridge_configured"] is True

        me = client.get("/api/auth/me").json()
        assert bridge["calls"][0][0] == "login"
        assert get_temu_session(me["id"])["status"] == "active"
        # 未勾选「记住密码」：凭据表不应有任何记录
        assert get_temu_credential(me["id"]) is None
    finally:
        client.__exit__(None, None, None)


def test_remember_password_roundtrip_and_one_click_relogin(bridge, monkeypatch):
    monkeypatch.setattr(credential_cipher, "CREDENTIAL_KEY", KEY)
    client = _client_for("store_login_b", "pass-login-b")
    try:
        response = _login_store(client, "demo3@example.com", "temu-secret-b", True)
        assert response.status_code == 200, response.text
        me = client.get("/api/auth/me").json()

        stored = get_temu_credential(me["id"])
        assert stored is not None
        assert stored["ciphertext"] != "temu-secret-b"
        assert credential_cipher.decrypt(stored["ciphertext"]) == "temu-secret-b"

        # 退出后仍可用已保存凭据一键重登（密码不经过前端）
        assert client.post("/api/store/session/logout").status_code == 200
        assert get_temu_session(me["id"])["status"] == "logged_out"
        relogin = client.post("/api/store/session/login", json={"use_remembered": True})
        assert relogin.status_code == 200, relogin.text
        assert relogin.json()["status"] == "active"

        # 清除已保存密码
        assert client.delete("/api/store/session/remember").json()["cleared"] is True
        assert get_temu_credential(me["id"]) is None
        missing = client.post("/api/store/session/login", json={"use_remembered": True})
        assert missing.status_code == 400
    finally:
        client.__exit__(None, None, None)


def test_switch_store_auto_releases_previous_session(bridge):
    """换店铺不再要求先手动退出：自动释放旧会话后登录新账号。

    历史行为是 409「请先退出当前店铺」，但前端常出现「界面已失效、后端仍 active」
    的不一致状态，用户会被卡死。现在换成自动释放。
    """
    client = _client_for("store_login_c", "pass-login-c")
    try:
        assert _login_store(client, "demo4@example.com", "pw-c1").status_code == 200
        second = _login_store(client, "demo5@example.com", "pw-c2")
        assert second.status_code == 200
        assert second.json()["account_masked"] != ""
        # 旧店铺应该被释放
        assert any(call[0] == "logout" and call[1] == "demo4@example.com" for call in bridge["calls"])
    finally:
        client.__exit__(None, None, None)


def test_same_store_relogin_still_requires_logout(bridge):
    """同一个店铺且确实在线时，仍要求先退出（避免重复建会话烧限流额度）。"""
    client = _client_for("store_login_c2", "pass-login-c2")
    try:
        assert _login_store(client, "demo4b@example.com", "pw-c1b").status_code == 200
        again = _login_store(client, "demo4b@example.com", "pw-c1b")
        assert again.status_code == 409
        assert "先退出" in again.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_same_temu_account_cannot_be_used_by_two_users(bridge):
    first = _client_for("store_login_d1", "pass-login-d1")
    try:
        assert _login_store(first, "demo6@example.com", "pw-d").status_code == 200
    finally:
        first.__exit__(None, None, None)

    second = _client_for("store_login_d2", "pass-login-d2")
    try:
        response = _login_store(second, "demo6@example.com", "pw-d")
        assert response.status_code == 409
        assert "已由" in response.json()["detail"]
    finally:
        second.__exit__(None, None, None)


def test_captcha_cooldown_is_reported_with_retry_after(bridge):
    client = _client_for("store_login_e", "pass-login-e")
    try:
        bridge["fail"] = temu_bridge.BridgeError("54001", "需要图形验证码", stage="login", status=429, retry_after=1800)
        response = _login_store(client, "demo7@example.com", "pw-e")
        assert response.status_code == 429
        assert response.headers.get("Retry-After") == "1800"
        me = client.get("/api/auth/me").json()
        session = get_temu_session(me["id"])
        assert session["status"] == "cooldown"
        assert session["cooldown_until"] is not None

        # 冷却期内再次登录被拦下（不再打 bridge）
        before = len(bridge["calls"])
        again = _login_store(client, "demo7@example.com", "pw-e")
        assert again.status_code == 429
        assert again.json()["detail"].startswith("需要图形验证码")
        assert len(bridge["calls"]) == before
    finally:
        client.__exit__(None, None, None)


def test_logout_is_idempotent_and_clears_remote_session(bridge):
    client = _client_for("store_login_f", "pass-login-f")
    try:
        assert _login_store(client, "demo8@example.com", "pw-f").status_code == 200
        first = client.post("/api/store/session/logout")
        assert first.status_code == 200
        assert first.json()["status"] == "logged_out"
        second = client.post("/api/store/session/logout")
        assert second.status_code == 200
        assert any(call[0] == "logout" for call in bridge["calls"])
        me = client.get("/api/auth/me").json()
        assert get_temu_session(me["id"])["account"] == ""
    finally:
        client.__exit__(None, None, None)


def test_refresh_marks_session_invalid_without_auto_relogin(bridge, monkeypatch):
    client = _client_for("store_login_g", "pass-login-g")
    try:
        assert _login_store(client, "demo9@example.com", "pw-g").status_code == 200
        monkeypatch.setattr(temu_bridge, "health", lambda account: {"alive": False})
        response = client.post("/api/store/session/refresh")
        assert response.status_code == 200
        assert response.json()["status"] == "invalid"
        # 失效不触发自动重登（否则会消耗风控配额）
        assert not any(call[0] == "login" for call in bridge["calls"][1:])
    finally:
        client.__exit__(None, None, None)


def test_unconfigured_bridge_returns_503(monkeypatch):
    client = _client_for("store_login_h", "pass-login-h")
    try:
        monkeypatch.setattr(temu_bridge, "configured", lambda: False)
        response = _login_store(client, "demo10@example.com", "pw-h")
        assert response.status_code == 503
        assert client.get("/api/store/session").json()["bridge_configured"] is False
    finally:
        client.__exit__(None, None, None)


def test_expired_cooldown_is_healed_to_logged_out():
    """冷却到期必须复位：否则 status 停在 cooldown，前端永久禁用登录、倒计时显示 0 分 00 秒。"""
    from datetime import datetime, timedelta, timezone

    user = create_user("cooldown_heal", hash_password("pass-heal-1"), status="approved", store_access=True)
    upsert_temu_session(
        user["id"],
        account="heal@example.com",
        account_hash=store_session.account_hash("heal@example.com"),
        account_masked=store_session.mask_account("heal@example.com"),
        status="cooldown",
        cooldown_until=datetime.now(timezone.utc) - timedelta(minutes=5),
        last_error_code="RATE_LIMIT",
        last_error="[login] 登录间隔过短，请稍后再试",
    )

    data = store_session.view(user["id"])
    assert data["status"] == "logged_out"
    assert data["cooldown_seconds"] == 0
    # 库里也要复位，避免管理员视图 / 下次登录继续卡住
    assert get_temu_session(user["id"])["status"] == "logged_out"


def test_active_cooldown_is_not_healed():
    from datetime import datetime, timedelta, timezone

    user = create_user("cooldown_alive", hash_password("pass-heal-2"), status="approved", store_access=True)
    upsert_temu_session(
        user["id"],
        account="alive@example.com",
        account_hash=store_session.account_hash("alive@example.com"),
        account_masked=store_session.mask_account("alive@example.com"),
        status="cooldown",
        cooldown_until=datetime.now(timezone.utc) + timedelta(minutes=10),
        last_error_code="54001",
    )

    data = store_session.view(user["id"])
    assert data["status"] == "cooldown"
    assert data["cooldown_seconds"] > 0
