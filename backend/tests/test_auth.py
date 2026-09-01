from fastapi.testclient import TestClient

from app.database import create_user
from app.main import app
from app.services.auth import hash_password


def test_registration_requires_admin_approval():
    with TestClient(app) as client:
        username = "pending_user_01"
        response = client.post("/api/auth/register", json={"username": username, "password": "password123"})
        assert response.status_code == 200

        login = client.post("/api/auth/login", json={"username": username, "password": "password123"})
        assert login.status_code == 403
        assert "审核" in login.json()["detail"]


def test_admin_can_approve_user_and_change_settings():
    admin = create_user("admin_test", hash_password("adminpass123"), role="admin", status="approved")
    user = create_user("approval_user", hash_password("userpass123"))
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "admin_test", "password": "adminpass123"})
        assert login.status_code == 200
        users = client.get("/api/admin/users")
        assert users.status_code == 200
        approved = client.post(f"/api/admin/users/{user['id']}/approve")
        assert approved.status_code == 200
        settings = client.get("/api/admin/settings")
        assert settings.status_code == 200
        payload = settings.json()
        payload["order"]["operation_fee"] = 8
        saved = client.put("/api/admin/settings", json=payload)
        assert saved.status_code == 200
        assert saved.json()["order"]["operation_fee"] == 8
