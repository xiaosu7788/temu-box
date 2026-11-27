"""建品记录接口：GET /api/store/product-logs。

回归点：main.py 是 `from app.database import (...)` 的函数式导入，
曾误写成 `database.list_product_create_logs(...)`，NameError 直接 500。
"""

from fastapi.testclient import TestClient

from app.database import create_user, list_users, log_product_create
from app.main import app
from app.services.auth import hash_password

ADMIN = "product_logs_admin"
PASSWORD = "previewpass123"


def _login(client: TestClient) -> int:
    """登录管理员，返回其 user_id（记录要按这个 id 写，否则接口查不到）。"""
    uid = None
    for row in list_users():
        if row["username"] == ADMIN:
            uid = int(row["id"])
            break
    if uid is None:
        created = create_user(ADMIN, hash_password(PASSWORD), role="admin", status="approved", store_access=True)
        uid = int(created["id"])
    r = client.post("/api/auth/login", json={"username": ADMIN, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return uid


def test_product_logs_returns_items():
    with TestClient(app) as client:
        uid = _login(client)
        log_product_create(uid, "回归-成功", status="ok")
        log_product_create(uid, "回归-失败", status="failed", error="6000002 属性缺失")

        r = client.get("/api/store/product-logs")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] >= 2, body
        assert body["failed"] >= 1, body
        assert "回归-失败" in [x["product_name"] for x in body["items"]]


def test_product_logs_filter_failed():
    with TestClient(app) as client:
        uid = _login(client)
        log_product_create(uid, "筛-失败", status="failed", error="boom")

        r = client.get("/api/store/product-logs", params={"status": "failed"})
        assert r.status_code == 200, r.text
        assert all(x["status"] == "failed" for x in r.json()["items"])
