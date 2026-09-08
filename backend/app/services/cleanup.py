"""Automatic cleanup of expired task/activity files and audit logs."""
from __future__ import annotations

import json
import logging
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import ACTIVITY_DIR, CLEANUP_INTERVAL_SECONDS, TASKS_DIR
from app.database import (
    AuditLog,
    SessionLocal,
    delete_activity_job,
    delete_task_record,
    list_all_activity_jobs,
    load_task_records,
    prune_audit_logs,
)
from app.services import system
from app.services.activity_tasks import activity_task_manager
from app.services.tasks import task_manager

logger = logging.getLogger("temubox.cleanup")


def _parse_local_time(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def _parse_utc_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    # SQLite 存取会丢失时区信息，统一按 UTC 处理以便与 aware cutoff 比较
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _remove_dir(path: Path) -> bool:
    try:
        if path.exists():
            shutil.rmtree(path)
            return True
    except OSError:
        logger.exception("Cannot remove directory: %s", path)
    return False


def _cleanup_tasks(cutoff: datetime) -> int:
    removed = 0
    valid_ids = set()
    for task in load_task_records():
        task_id = task.get("id")
        if not task_id:
            continue
        valid_ids.add(task_id)
        if task.get("status") in {"preparing", "queued", "running"}:
            continue
        created = _parse_local_time(task.get("created_at", ""))
        if created is not None and created < cutoff:
            _remove_dir(TASKS_DIR / task_id)
            delete_task_record(task_id, None)
            task_manager.drop(task_id)
            removed += 1
    # 清理没有任何任务记录的孤儿目录（如上传中途崩溃残留）
    for directory in TASKS_DIR.glob("*"):
        if not directory.is_dir() or directory.name in valid_ids:
            continue
        if not (directory / "task.json").exists():
            try:
                if datetime.fromtimestamp(directory.stat().st_mtime) < cutoff:
                    if _remove_dir(directory):
                        removed += 1
            except OSError:
                continue
    return removed


def _cleanup_activities(cutoff_utc: datetime) -> int:
    removed = 0
    valid_ids = set()
    for job in list_all_activity_jobs(limit=100000):
        job_id = job.get("id")
        if not job_id:
            continue
        valid_ids.add(job_id)
        if job.get("status") in {"queued", "running"}:
            continue
        created = _parse_utc_time(job.get("created_at"))
        if created is not None and created < cutoff_utc:
            _remove_dir(ACTIVITY_DIR / job_id)
            delete_activity_job(job_id, None)
            activity_task_manager.drop(job_id)
            removed += 1
    for directory in ACTIVITY_DIR.glob("*"):
        if not directory.is_dir() or directory.name in valid_ids:
            continue
        try:
            if datetime.fromtimestamp(directory.stat().st_mtime, tz=timezone.utc) < cutoff_utc:
                if _remove_dir(directory):
                    removed += 1
        except OSError:
            continue
    return removed


def run_once() -> dict:
    settings = system.get()
    if not settings["cleanup_enabled"]:
        return {"skipped": True, "reason": "cleanup_disabled"}
    now = datetime.now()
    cutoff = now - timedelta(days=settings["cleanup_retention_days"])
    cutoff_utc = datetime.now(timezone.utc) - timedelta(days=settings["cleanup_retention_days"])
    result = {
        "removed_task_dirs": _cleanup_tasks(cutoff),
        "removed_activity_dirs": _cleanup_activities(cutoff_utc),
        "pruned_audit_logs": prune_audit_logs(datetime.now(timezone.utc) - timedelta(days=settings["audit_retention_days"])),
        "retention_days": settings["cleanup_retention_days"],
    }
    if any(result[key] for key in ("removed_task_dirs", "removed_activity_dirs", "pruned_audit_logs")):
        logger.info("Cleanup finished: %s", json.dumps(result, ensure_ascii=False))
    return result


def purge_all() -> dict:
    """立即清空全部历史数据（忽略保留期）；排队/运行中的任务始终保留。"""
    removed_tasks = 0
    for task in load_task_records():
        task_id = task.get("id")
        if not task_id or task.get("status") in {"preparing", "queued", "running"}:
            continue
        if _remove_dir(TASKS_DIR / task_id):
            delete_task_record(task_id, None)
            task_manager.drop(task_id)
            removed_tasks += 1

    removed_activities = 0
    for job in list_all_activity_jobs(limit=100000):
        job_id = job.get("id")
        if not job_id or job.get("status") in {"queued", "running"}:
            continue
        if _remove_dir(ACTIVITY_DIR / job_id):
            delete_activity_job(job_id, None)
            activity_task_manager.drop(job_id)
            removed_activities += 1

    with SessionLocal.begin() as session:
        removed_audit_logs = session.query(AuditLog).delete(synchronize_session=False)

    result = {
        "removed_task_dirs": removed_tasks,
        "removed_activity_dirs": removed_activities,
        "pruned_audit_logs": removed_audit_logs,
        "retention_days": 0,
    }
    logger.info("Purge finished: %s", json.dumps(result, ensure_ascii=False))
    return result


class CleanupScheduler:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.last_run: dict | None = None
        self.last_run_at: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="cleanup-scheduler", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(CLEANUP_INTERVAL_SECONDS):
            try:
                self.last_run = run_once()
                self.last_run_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                logger.exception("Scheduled cleanup failed")


cleanup_scheduler = CleanupScheduler()
