"""Runtime monitoring: disk, memory, process and task queue metrics."""
from __future__ import annotations

import shutil
from pathlib import Path

from app.config import ACTIVITY_DIR, DATA_DIR, TASKS_DIR
from app.database import activity_job_status_counts, database_status, task_status_counts
from app.services.activity_tasks import activity_task_manager
from app.services.tasks import task_manager

try:
    import psutil
except ImportError:  # psutil 未安装时内存指标降级为不可用
    psutil = None


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _human_bytes(value: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{value:.1f}TB"


def _memory_snapshot() -> dict:
    if psutil is None:
        return {"available": False}
    virtual = psutil.virtual_memory()
    process = psutil.Process()
    return {
        "available": True,
        "total": virtual.total,
        "used": virtual.used,
        "free": virtual.available,
        "percent": virtual.percent,
        "process_rss": process.memory_info().rss,
    }


def snapshot() -> dict:
    disk = shutil.disk_usage(DATA_DIR)
    tasks_size = _dir_size(TASKS_DIR)
    activities_size = _dir_size(ACTIVITY_DIR)
    return {
        "disk": {
            "path": str(DATA_DIR),
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percent": round(disk.used / disk.total * 100, 1) if disk.total else 0,
        },
        "memory": _memory_snapshot(),
        "task_pools": {
            "orders": task_manager.pool_stats(),
            "activities": activity_task_manager.pool_stats(),
        },
        "task_counts": {
            "orders": task_status_counts(),
            "activities": activity_job_status_counts(),
        },
        "storage": {
            "tasks_dir": str(TASKS_DIR),
            "tasks_bytes": tasks_size,
            "tasks_size": _human_bytes(tasks_size),
            "activities_dir": str(ACTIVITY_DIR),
            "activities_bytes": activities_size,
            "activities_size": _human_bytes(activities_size),
        },
        "database": database_status(),
    }
