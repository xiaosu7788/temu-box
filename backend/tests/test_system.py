import threading
import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import ACTIVITY_DIR, TASKS_DIR
from app.database import create_activity_job, create_user, get_activity_job, load_task_records, update_activity_job
from app.main import app
from app.services import system
from app.services.activity_tasks import ActivityTaskManager, activity_task_manager
from app.services.auth import hash_password
from app.services.cleanup import run_once
from app.services.taskpool import QueueFullError, TaskPool
from app.services.tasks import task_manager


def _drain(pool: TaskPool, timeout: float = 10) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline and pool.stats()["queued"] > 0:
        time.sleep(0.02)


def test_task_pool_enforces_queue_limit():
    pool = TaskPool("test-limit", 1, 2, max_threads=4)
    release = threading.Event()
    try:
        pool.submit(lambda: release.wait(5))
        pool.submit(lambda: release.wait(5))
        with pytest.raises(QueueFullError):
            pool.submit(lambda: None)
    finally:
        release.set()
        _drain(pool)


def test_task_pool_respects_concurrency_and_hot_reconfigure():
    pool = TaskPool("test-conc", 2, 10, max_threads=8)
    active = 0
    peak = 0
    lock = threading.Lock()

    def work():
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1

    for _ in range(6):
        pool.submit(work)
    _drain(pool)
    assert peak <= 2

    pool.apply(4, 20)
    stats = pool.stats()
    assert stats["workers"] == 4
    assert stats["queue_limit"] == 20


def test_activity_manager_rejects_create_when_queue_full():
    system.update({"activity_workers": 1, "activity_queue_limit": 1})
    activity_task_manager.apply_pool_settings(system.get())
    release = threading.Event()
    try:
        activity_task_manager._pool.submit(lambda: release.wait(5))
        with pytest.raises(QueueFullError):
            activity_task_manager.create("demo.xlsx", 1, b"x", None)
    finally:
        release.set()
        _drain(activity_task_manager._pool)
    system.update({"activity_workers": 2, "activity_queue_limit": 50})
    activity_task_manager.apply_pool_settings(system.get())


def test_activity_jobs_are_requeued_after_restart():
    snapshot = {"region": {"code": "US", "name": "美国区"}, "versions": {"activity": 1}, "settings": {"activity": {"headcost": 5}}}
    job = create_activity_job("recover-running", "demo.xlsx", 1, snapshot)
    update_activity_job(job["id"], status="running", progress=50, message="处理中")
    job_dir = ACTIVITY_DIR / job["id"]
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "input.xlsx").write_bytes(b"not-an-excel")

    ActivityTaskManager()  # 模拟服务重启

    deadline = time.time() + 10
    current = get_activity_job(job["id"])
    while time.time() < deadline and current["status"] not in {"failed", "completed"}:
        time.sleep(0.1)
        current = get_activity_job(job["id"])
    # 输入文件无效：任务被重新执行后失败，而不是永远卡在 running
    assert current["status"] == "failed"


def test_activity_jobs_without_input_marked_failed_after_restart():
    snapshot = {"region": {"code": "US", "name": "美国区"}, "versions": {"activity": 1}, "settings": {"activity": {"headcost": 5}}}
    job = create_activity_job("recover-missing", "demo.xlsx", 1, snapshot)
    update_activity_job(job["id"], status="queued", progress=5, message="任务已进入处理队列")

    ActivityTaskManager()  # 模拟服务重启（input.xlsx 不存在）

    current = get_activity_job(job["id"])
    assert current["status"] == "failed"
    assert "无法恢复" in current["message"]


def test_admin_can_view_and_update_system_settings_with_live_pool_apply():
    create_user("system_settings_admin", hash_password("adminpass123"), role="admin", status="approved")
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "system_settings_admin", "password": "adminpass123"}).status_code == 200
        current = client.get("/api/admin/system-settings")
        assert current.status_code == 200
        payload = current.json()["settings"]
        payload["task_workers"] = 3
        payload["task_queue_limit"] = 77
        saved = client.put("/api/admin/system-settings", json=payload)
        assert saved.status_code == 200
        assert saved.json()["settings"]["task_queue_limit"] == 77
        # 并发/队列上限热更新立即生效
        assert task_manager.pool_stats()["queue_limit"] == 77
        assert task_manager.pool_stats()["workers"] == 3
        # Pydantic 边界校验
        bad = dict(payload)
        bad["task_workers"] = 99
        assert client.put("/api/admin/system-settings", json=bad).status_code == 422
    system.update({"task_workers": 2, "task_queue_limit": 50})
    task_manager.apply_pool_settings(system.get())


def test_regular_user_cannot_access_system_admin_endpoints():
    create_user("system_reader", hash_password("readerpass123"), status="approved")
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "system_reader", "password": "readerpass123"}).status_code == 200
        assert client.get("/api/admin/system-settings").status_code == 403
        assert client.get("/api/admin/audit-logs").status_code == 403
        assert client.get("/api/admin/monitoring").status_code == 403


def test_cleanup_removes_expired_task_records_and_directories():
    task = task_manager.create("old-sales.xlsx", "old-delivery.xlsx", None, 1)
    task_manager._update(task["id"], status="completed", progress=100, message="处理完成")
    old_text = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")
    with task_manager._lock:
        task_manager._tasks[task["id"]]["created_at"] = old_text
        task_manager._write(task_manager._tasks[task["id"]])

    recent = task_manager.create("new-sales.xlsx", "new-delivery.xlsx", None, 1)
    task_manager._update(recent["id"], status="completed", progress=100, message="处理完成")

    system.update({"cleanup_enabled": True, "cleanup_retention_days": 30})
    result = run_once()

    assert result["removed_task_dirs"] >= 1
    assert task["id"] not in {record["id"] for record in load_task_records()}
    assert not (TASKS_DIR / task["id"]).exists()
    assert task_manager.get(task["id"]) is None
    # 未过期的任务不受影响
    assert recent["id"] in {record["id"] for record in load_task_records()}


def test_cleanup_respects_enabled_toggle():
    system.update({"cleanup_enabled": False})
    assert run_once()["skipped"] is True
    system.update({"cleanup_enabled": True})


def test_audit_log_records_login_and_admin_operations():
    create_user("audit_admin", hash_password("auditpass123"), role="admin", status="approved")
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "audit_admin", "password": "auditpass123"}).status_code == 200
        logs = client.get("/api/admin/audit-logs")
        assert logs.status_code == 200
        actions = {item["action"] for item in logs.json()["items"]}
        assert "auth.login" in actions
        assert logs.json()["actions"]  # 动作清单用于筛选下拉

        # 失败登录也会被审计
        client.post("/api/auth/logout")
        assert client.post("/api/auth/login", json={"username": "audit_admin", "password": "wrong-password"}).status_code == 401
        client.post("/api/auth/login", json={"username": "audit_admin", "password": "auditpass123"})
        failed = client.get("/api/admin/audit-logs", params={"action": "auth.login_failed"}).json()
        assert failed["total"] >= 1

        keyword = client.get("/api/admin/audit-logs", params={"keyword": "登录成功"}).json()
        assert keyword["total"] >= 1


def test_monitoring_endpoint_exposes_runtime_metrics():
    create_user("monitor_admin", hash_password("monitorpass123"), role="admin", status="approved")
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "monitor_admin", "password": "monitorpass123"}).status_code == 200
        data = client.get("/api/admin/monitoring").json()
        assert 0 <= data["disk"]["percent"] <= 100
        assert data["disk"]["total"] > 0
        assert data["task_pools"]["orders"]["workers"] >= 1
        assert data["task_pools"]["activities"]["workers"] >= 1
        assert "orders" in data["task_counts"]
        assert data["database"]["status"] == "ok"
        assert "tasks_size" in data["storage"]
