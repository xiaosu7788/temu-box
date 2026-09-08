from __future__ import annotations

import logging
import shutil
import threading
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Optional

from app.config import ACTIVITY_DIR
from app.database import create_activity_job, delete_activity_job, get_activity_job, list_activity_jobs, list_activity_jobs_by_status, list_all_activity_jobs, update_activity_job
from app.services import system
from app.services.activity import normalize_id_profit_rules, normalize_parse_config, process_activity_workbook, settings_allowed_pieces
from app.services.regions import region_snapshot
from app.services.taskpool import QueueFullError, TaskPool


logger = logging.getLogger("temubox.activity_tasks")


class ActivityTaskManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}
        settings = system.get()
        self._pool = TaskPool("activity-task", settings["activity_workers"], settings["activity_queue_limit"])
        self._recover_interrupted()

    def apply_pool_settings(self, settings: dict) -> None:
        self._pool.apply(settings["activity_workers"], settings["activity_queue_limit"])

    def pool_stats(self) -> dict:
        return self._pool.stats()

    def queue_full(self) -> bool:
        return self._pool.full()

    def _recover_interrupted(self) -> None:
        """服务重启后恢复中断的活动任务。

        活动任务的输入文件与配置快照均已持久化（磁盘 input.xlsx + 数据库
        config_snapshot），因此 queued/running 状态的任务可以安全地重新排队；
        输入文件或配置缺失的任务直接标记为失败。
        """
        recovered = 0
        for job in list_activity_jobs_by_status(("queued", "running")):
            job_id = job["id"]
            snapshot = job.get("config_snapshot") or {}
            input_path = ACTIVITY_DIR / job_id / "input.xlsx"
            if not input_path.exists() or not snapshot.get("settings"):
                self._update(job_id, status="failed", progress=100, message="服务重启后无法恢复任务：输入文件或配置缺失", stats={"error": "recovery_failed"})
                logger.warning("Activity job %s cannot be recovered: missing input or snapshot", job_id)
                continue
            self._update(job_id, status="queued", progress=5, message="服务重启，任务已自动重新排队")
            try:
                self._pool.submit(lambda jid=job_id, snap=snapshot: self._run(jid, snap))
                recovered += 1
            except QueueFullError:
                self._update(job_id, status="failed", progress=100, message="服务重启恢复失败：任务队列已满", stats={"error": "queue_full"})
        if recovered:
            logger.info("Recovered %d interrupted activity job(s) after restart", recovered)

    def create(self, filename: str, owner_id: int, content: bytes, snapshot: dict | None = None, uplift_limit: float | None = None, parse_config: dict | None = None, id_profit_rules: list[dict] | None = None) -> dict:
        if self._pool.full():
            raise QueueFullError(f"任务队列已满（上限 {self._pool.stats()['queue_limit']} 个），请稍后再试")
        job_id = uuid.uuid4().hex
        job_dir = ACTIVITY_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "input.xlsx").write_bytes(content)
        allowed_pieces = settings_allowed_pieces(snapshot or {})
        normalized_parse_config = normalize_parse_config(parse_config, allowed_pieces=allowed_pieces) if parse_config is not None else None
        normalized_id_profit_rules = normalize_id_profit_rules(id_profit_rules) if id_profit_rules is not None else None
        task_snapshot = deepcopy(snapshot or {})
        task_snapshot["task_overrides"] = {
            "uplift_limit": uplift_limit,
            "skc_rules": normalized_parse_config,
            "id_profit_rules": normalized_id_profit_rules,
        }
        job = create_activity_job(job_id, filename, owner_id, task_snapshot)
        with self._lock:
            self._jobs[job_id] = job
        try:
            self._pool.submit(lambda: self._run(job_id, task_snapshot))
        except QueueFullError as exc:
            self._update(job_id, status="failed", progress=100, message=f"任务提交失败：{exc}", stats={"error": "queue_full"})
        return self.public(job)

    def _update(self, job_id: str, **values) -> None:
        job = update_activity_job(job_id, **values)
        if job:
            with self._lock:
                self._jobs[job_id] = job

    def _run(self, job_id: str, snapshot: dict) -> None:
        input_path = ACTIVITY_DIR / job_id / "input.xlsx"
        output_path = ACTIVITY_DIR / job_id / "批量报名活动处理结果.xlsx"
        try:
            self._update(job_id, status="running", progress=15, message="正在计算活动价格")
            settings = deepcopy(snapshot["settings"])
            overrides = snapshot.get("task_overrides", {})
            uplift_limit = overrides.get("uplift_limit")
            parse_config = overrides.get("skc_rules")
            id_profit_rules = overrides.get("id_profit_rules")
            if uplift_limit is not None:
                settings = deepcopy(settings)
                settings["activity"]["uplift_limit"] = uplift_limit
            stats = process_activity_workbook(input_path.read_bytes(), output_path, settings, parse_config, id_profit_rules)
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
        return {key: job.get(key) for key in ("id", "status", "progress", "message", "filename", "created_at", "stats", "logs", "output_path", "region_code", "region_name", "category_code", "category_name", "config_version")} | {"download_ready": job.get("status") == "completed" and bool(job.get("output_path"))}

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

    def drop(self, job_id: str) -> None:
        """仅移除内存缓存（磁盘与数据库记录由清理服务负责）。"""
        with self._lock:
            self._jobs.pop(job_id, None)


activity_task_manager = ActivityTaskManager()
