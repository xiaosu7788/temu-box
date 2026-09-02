from __future__ import annotations

import logging
import shutil
import threading
import uuid
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from app.config import ACTIVITY_DIR, TASK_WORKERS
from app.database import create_activity_job, delete_activity_job, get_activity_job, list_activity_jobs, list_all_activity_jobs, update_activity_job
from app.services.activity import normalize_parse_config, process_activity_workbook
from app.services.regions import region_snapshot


logger = logging.getLogger("sales_tool.activity_tasks")


class ActivityTaskManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}
        self._executor = ThreadPoolExecutor(max_workers=TASK_WORKERS, thread_name_prefix="activity-task")

    def create(self, filename: str, owner_id: int, content: bytes, snapshot: dict | None = None, uplift_limit: float | None = None, parse_config: dict | None = None) -> dict:
        job_id = uuid.uuid4().hex
        job_dir = ACTIVITY_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "input.xlsx").write_bytes(content)
        job = create_activity_job(job_id, filename, owner_id, snapshot)
        with self._lock:
            self._jobs[job_id] = job
        normalized_parse_config = normalize_parse_config(parse_config)
        self._executor.submit(self._run, job_id, snapshot, uplift_limit, normalized_parse_config)
        return self.public(job)

    def _update(self, job_id: str, **values) -> None:
        job = update_activity_job(job_id, **values)
        if job:
            with self._lock:
                self._jobs[job_id] = job

    def _run(self, job_id: str, snapshot: dict, uplift_limit: float | None, parse_config: dict | None) -> None:
        input_path = ACTIVITY_DIR / job_id / "input.xlsx"
        output_path = ACTIVITY_DIR / job_id / "批量报名活动处理结果.xlsx"
        try:
            self._update(job_id, status="running", progress=15, message="正在计算活动价格")
            settings = deepcopy(snapshot["settings"])
            if uplift_limit is not None:
                settings = deepcopy(settings)
                settings["activity"]["uplift_limit"] = uplift_limit
            stats = process_activity_workbook(input_path.read_bytes(), output_path, settings, parse_config)
            self._update(job_id, status="completed", progress=100, message="处理完成", output_path=str(output_path), stats=stats)
        except Exception as exc:
            logger.exception("Activity task failed: %s", job_id)
            self._update(job_id, status="failed", progress=100, message=f"处理失败：{exc}", stats={"error": str(exc)})

    def get(self, job_id: str, owner_id: Optional[int]) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
        if job and job.get("owner_id") == owner_id:
            return self.public(job)
        return self.public(get_activity_job(job_id, owner_id))

    def list(self, owner_id: Optional[int], limit: int = 50) -> list[dict]:
        jobs = list_activity_jobs(owner_id, limit)
        with self._lock:
            for job in jobs:
                self._jobs[job["id"]] = job
        return [self.public(job) for job in jobs]

    def list_admin(self, limit: int = 100) -> list[dict]:
        return [self.public(job) | {"owner_id": job.get("owner_id")} for job in list_all_activity_jobs(limit)]

    @staticmethod
    def public(job: dict | None) -> dict | None:
        if not job:
            return None
        return {key: job.get(key) for key in ("id", "status", "progress", "message", "filename", "created_at", "stats", "logs", "output_path", "region_code", "region_name", "config_version")} | {"download_ready": job.get("status") == "completed" and bool(job.get("output_path"))}

    def result_path(self, job_id: str, owner_id: Optional[int]) -> Path | None:
        job = self.get(job_id, owner_id)
        if not job or not job.get("download_ready"):
            return None
        path = Path(job["output_path"])
        return path if path.is_file() else None

    def delete(self, job_id: str, owner_id: Optional[int]) -> str:
        result = delete_activity_job(job_id, owner_id)
        if result != "deleted":
            return result
        with self._lock:
            self._jobs.pop(job_id, None)
        job_dir = ACTIVITY_DIR / job_id
        if job_dir.exists():
            shutil.rmtree(job_dir)
        return "deleted"


activity_task_manager = ActivityTaskManager()
