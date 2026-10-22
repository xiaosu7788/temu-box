from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.database import create_user
from app.main import app
from app.services.auth import hash_password


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text


def test_admin_can_edit_display_name_and_private_note():
    admin = create_user("user_note_admin", hash_password("{{SECRET_E7hN1x9Q}}"), role="admin", status="approved")
    target = create_user("user_note_target", hash_password("{{SECRET_G4pL8v2M}}"), display_name="旧名", status="approved")
    with TestClient(app) as client:
        _login(client, admin["username"], "{{SECRET_E7hN1x9Q}}")
        response = client.patch(
            f"/api/admin/users/{target['id']}",
            json={"display_name": "新显示名", "note": "仅管理员可见"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["display_name"] == "新显示名"
        assert response.json()["note"] == "仅管理员可见"
        assert response.json()["created_at"].endswith("+00:00")
        listed = client.get("/api/admin/users").json()["items"]
        saved = next(item for item in listed if item["id"] == target["id"])
        assert saved["note"] == "仅管理员可见"

        # 备注不会通过普通用户的 /me 接口泄露。
        client.post("/api/auth/logout")
        _login(client, "user_note_target", "{{SECRET_G4pL8v2M}}")
        assert "note" not in client.get("/api/auth/me").json()


def test_admin_bulk_update_disable_and_delete_are_atomic():
    admin = create_user("user_bulk_admin", hash_password("{{SECRET_T8bQ4m6C}}"), role="admin", status="approved")
    first = create_user("user_bulk_first", hash_password("{{SECRET_R2nV5x8D}}"), status="approved")
    second = create_user("user_bulk_second", hash_password("{{SECRET_P6cK9w3A}}"), status="pending")
    with TestClient(app) as client:
        _login(client, admin["username"], "{{SECRET_T8bQ4m6C}}")
        updated = client.patch(
            "/api/admin/users/bulk",
            json={"user_ids": [first["id"], second["id"]], "note": "批量备注", "status": "rejected"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["updated"] == 2
        assert {item["note"] for item in updated.json()["items"]} == {"批量备注"}
        assert {item["status"] for item in updated.json()["items"]} == {"rejected"}

        disabled_login = client.post("/api/auth/login", json={"username": "user_bulk_first", "password": "{{SECRET_R2nV5x8D}}"})
        assert disabled_login.status_code == 403
        assert disabled_login.json()["detail"] == "账号已禁用"

        blocked = client.patch("/api/admin/users/bulk", json={"user_ids": [first["id"], admin["id"]], "note": "不应部分成功"})
        assert blocked.status_code == 400
        listed = client.get("/api/admin/users").json()["items"]
        first_after = next(item for item in listed if item["id"] == first["id"])
        assert first_after["note"] == "批量备注"

        deleted = client.post("/api/admin/users/bulk-delete", json={"user_ids": [first["id"], second["id"]]})
        assert deleted.status_code == 200, deleted.text
        assert set(deleted.json()["deleted_ids"]) == {first["id"], second["id"]}
        remaining_ids = {item["id"] for item in client.get("/api/admin/users").json()["items"]}
        assert admin["id"] in remaining_ids
        assert first["id"] not in remaining_ids
        assert second["id"] not in remaining_ids


def test_user_timestamp_normalizes_naive_sqlite_values_as_utc():
    from app.database import _user_timestamp

    assert _user_timestamp(datetime(2026, 10, 22, 1, 58, 54)) == "2026-10-22T01:58:54+00:00"
    assert _user_timestamp(datetime(2026, 10, 22, 1, 58, 54, tzinfo=timezone.utc)) == "2026-10-22T01:58:54+00:00"
