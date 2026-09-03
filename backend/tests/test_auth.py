from fastapi.testclient import TestClient

from app.database import create_activity_job, create_user, update_activity_job
from app.main import app
from app.services.auth import hash_password
from app.services.tasks import task_manager


def test_registration_requires_admin_approval():
    with TestClient(app) as client:
        username = "pending_user_01"
        response = client.post("/api/auth/register", json={"username": username, "password": "password123"})
        assert response.status_code == 200

        login = client.post("/api/auth/login", json={"username": username, "password": "password123"})
        assert login.status_code == 403
        assert "审核" in login.json()["detail"]


def test_admin_can_see_new_registration():
    create_user("admin_visibility", hash_password("adminpass123"), role="admin", status="approved")
    with TestClient(app) as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "visible_pending", "password": "password123"},
        )
        assert registered.status_code == 200

        login = client.post(
            "/api/auth/login",
            json={"username": "admin_visibility", "password": "adminpass123"},
        )
        assert login.status_code == 200
        users = client.get("/api/admin/users")
        assert users.status_code == 200
        visible = next(item for item in users.json()["items"] if item["username"] == "visible_pending")
        assert visible["status"] == "pending"


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


def test_approved_user_can_view_but_not_update_cost_settings():
    create_user("settings_reader", hash_password("readerpass123"), status="approved")
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "settings_reader", "password": "readerpass123"})
        assert login.status_code == 200
        settings = client.get("/api/settings")
        assert settings.status_code == 200
        assert settings.json()["order"]["tail_fee"] == 0
        assert settings.json()["order"]["shipping_subsidy"] == 0
        assert client.put("/api/admin/settings", json=settings.json()).status_code == 403


def test_admin_can_manage_default_activity_skc_rules():
    create_user("activity_rules_admin", hash_password("rulesadmin123"), role="admin", status="approved")
    rules = {
        "set_keywords": ["bundle", ""],
        "set_mappings": [{"pattern": "四件组合", "pieces": 4}],
        "single_mode": "after_marker",
        "single_delimiter": "-",
        "single_marker": "price",
    }
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "activity_rules_admin", "password": "rulesadmin123"})
        saved = client.put("/api/admin/activity-settings/skc-rules", json=rules)
        assert saved.status_code == 200
        assert saved.json() == rules
        assert client.get("/api/admin/activity-settings/skc-rules").json() == rules
        assert client.get("/api/settings").json()["activity"]["default_skc_rules"] == rules


def test_regular_user_cannot_update_default_activity_skc_rules():
    create_user("activity_rules_user", hash_password("rulesuser123"), status="approved")
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "activity_rules_user", "password": "rulesuser123"})
        rules = client.get("/api/settings").json()["activity"]["default_skc_rules"]
        assert client.put("/api/admin/activity-settings/skc-rules", json=rules).status_code == 403

def test_admin_can_update_and_delete_regular_users():
    admin = create_user("user_manager", hash_password("managerpass123"), role="admin", status="approved")
    target = create_user("managed_user", hash_password("oldpassword123"), status="approved")
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": admin["username"], "password": "managerpass123"})
        assert login.status_code == 200
        updated = client.patch(
            f"/api/admin/users/{target['id']}",
            json={"username": "renamed_user", "password": "newpassword123"},
        )
        assert updated.status_code == 200
        assert updated.json()["username"] == "renamed_user"

        client.post("/api/auth/logout")
        assert client.post("/api/auth/login", json={"username": "managed_user", "password": "oldpassword123"}).status_code == 401
        assert client.post("/api/auth/login", json={"username": "renamed_user", "password": "newpassword123"}).status_code == 200

        client.post("/api/auth/logout")
        client.post("/api/auth/login", json={"username": admin["username"], "password": "managerpass123"})
        assert client.delete(f"/api/admin/users/{target['id']}").status_code == 200
        assert target["id"] not in {item["id"] for item in client.get("/api/admin/users").json()["items"]}
        assert client.delete(f"/api/admin/users/{admin['id']}").status_code == 404


def test_task_history_is_scoped_to_owner():
    first = task_manager.create("sales-a.xlsx", "delivery-a.xlsx", None, 101)
    second = task_manager.create("sales-b.xlsx", "delivery-b.xlsx", None, 202)
    assert first["id"] in {task["id"] for task in task_manager.list(owner_id=101)}
    assert second["id"] not in {task["id"] for task in task_manager.list(owner_id=101)}
    assert task_manager.get(second["id"], owner_id=101) is None


def test_user_tasks_are_scoped_and_admin_uses_admin_endpoints_for_all_tasks():
    admin = create_user("task_admin", hash_password("adminpass123"), role="admin", status="approved")
    user = create_user("task_owner", hash_password("userpass123"), status="approved")
    task = task_manager.create("sales-delete.xlsx", "delivery-delete.xlsx", None, user["id"])
    task_manager._update(task["id"], status="completed", progress=100, message="处理完成")
    admin_order_task = task_manager.create("sales-admin-delete.xlsx", "delivery-admin-delete.xlsx", None, user["id"])
    task_manager._update(admin_order_task["id"], status="failed", progress=100, message="处理失败")
    activity_task = create_activity_job("admin-delete-activity", "activity-admin-delete.xlsx", user["id"])
    update_activity_job(activity_task["id"], status="completed", progress=100, message="处理完成")

    with TestClient(app) as client:
        user_login = client.post("/api/auth/login", json={"username": "task_owner", "password": "userpass123"})
        assert user_login.status_code == 200
        deleted = client.delete(f"/api/tasks/{task['id']}")
        assert deleted.status_code == 200
        assert client.get(f"/api/tasks/{task['id']}").status_code == 404

        client.post("/api/auth/logout")
        admin_login = client.post("/api/auth/login", json={"username": "task_admin", "password": "adminpass123"})
        assert admin_login.status_code == 200
        assert client.get("/api/admin/tasks").status_code == 200
        assert client.get("/api/admin/activity-tasks").status_code == 200
        assert admin_order_task["id"] not in {item["id"] for item in client.get("/api/tasks").json()["items"]}
        assert activity_task["id"] not in {item["id"] for item in client.get("/api/activities").json()["items"]}
        assert client.delete(f"/api/tasks/{admin_order_task['id']}").status_code == 404
        assert client.delete(f"/api/activities/{activity_task['id']}").status_code == 404
        assert client.delete(f"/api/admin/tasks/{admin_order_task['id']}").status_code == 200
        assert client.delete(f"/api/admin/activity-tasks/{activity_task['id']}").status_code == 200
        assert admin_order_task["id"] not in {item["id"] for item in client.get("/api/admin/tasks").json()["items"]}
        assert activity_task["id"] not in {item["id"] for item in client.get("/api/admin/activity-tasks").json()["items"]}

        client.post("/api/auth/logout")
        other = create_user("task_other", hash_password("otherpass123"), status="approved")
        client.post("/api/auth/login", json={"username": "task_other", "password": "otherpass123"})
        assert client.get("/api/admin/tasks").status_code == 403
