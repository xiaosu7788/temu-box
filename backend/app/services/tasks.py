from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Optional

from app.config import TASK_HISTORY_LIMIT, TASK_WORKERS, TASKS_DIR
from app.database import load_task_records, save_task_record
from app.services.half_headcost import load_entries, merge_upload
from app.services.inventory import load_price_catalog
from app.services.orders import build_delivery_sku_map, generate_summary


logger = logging.getLogger("sales_tool.tasks")


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class TaskManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: Dict[str, dict] = {}
        self._executor = ThreadPoolExecutor(max_workers=TASK_WORKERS, thread_name_prefix="sales-task")
        self._load_existing()

    def _load_existing(self) -> None:
        TASKS_DIR.mkdir(parents=True, exist_ok=True)
        for task in load_task_records():
            if task.get("id"):
                self._tasks[task["id"]] = task
        for metadata_path in TASKS_DIR.glob("*/task.json"):
            try:
                task = json.loads(metadata_path.read_text(encoding="utf-8"))
                if task.get("status") in {"queued", "running"}:
                    task.update({
                        "status": "failed",
                        "message": "服务重启，任务已中断",
                        "finished_at": now_text(),
                    })
                    self._write(task)
                self._tasks[task["id"]] = task
                save_task_record(task)
            except (OSError, json.JSONDecodeError, KeyError):
                logger.exception("Cannot load task metadata: %s", metadata_path)

    def _task_dir(self, task_id: str) -> Path:
        return TASKS_DIR / task_id

    def _write(self, task: dict) -> None:
        task_dir = self._task_dir(task["id"])
        task_dir.mkdir(parents=True, exist_ok=True)
        target = task_dir / "task.json"
        temp = task_dir / "task.json.tmp"
        temp.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, target)
        save_task_record(task)

    def create(self, sales_name: str, delivery_name: str, half_name: Optional[str]) -> dict:
        task_id = uuid.uuid4().hex
        task = {
            "id": task_id,
            "status": "preparing",
            "progress": 0,
            "message": "正在接收文件",
            "created_at": now_text(),
            "started_at": None,
            "finished_at": None,
            "stats": {},
            "logs": [],
            "files": {
                "sales": sales_name,
                "delivery": delivery_name,
                "half_headcost": half_name,
            },
            "result_file": None,
        }
        with self._lock:
            self._tasks[task_id] = task
            self._write(task)
        return self.public(task)

    def file_path(self, task_id: str, file_kind: str) -> Path:
        names = {"sales": "sales.xlsx", "delivery": "delivery.xlsx", "half_headcost": "half_headcost.xlsx"}
        return self._task_dir(task_id) / names[file_kind]

    def queue(self, task_id: str) -> None:
        self._update(task_id, status="queued", progress=5, message="任务已进入处理队列")
        self._executor.submit(self._run, task_id)

    def _update(self, task_id: str, **values) -> dict:
        with self._lock:
            task = self._tasks[task_id]
            task.update(values)
            self._write(task)
            return dict(task)

    def _log(self, task_id: str, message: str, progress: Optional[int] = None) -> None:
        line = f"{time.strftime('%H:%M:%S')}  {message}"
        with self._lock:
            task = self._tasks[task_id]
            task["logs"] = (task.get("logs", []) + [line])[-100:]
            task["message"] = message
            if progress is not None:
                task["progress"] = progress
            self._write(task)
        logger.info("task=%s %s", task_id, message)

    def _run(self, task_id: str) -> None:
        try:
            self._update(task_id, status="running", started_at=now_text())
            self._log(task_id, "正在加载库存数据", 15)
            catalog = load_price_catalog(log=lambda message: self._log(task_id, message, 35))
            task = self._tasks[task_id]
            half_path = self.file_path(task_id, "half_headcost")
            if task["files"].get("half_headcost") and half_path.exists():
                result = merge_upload(half_path)
                self._log(
                    task_id,
                    f"头程减半名单已合并：新增 {result['added']} 个，当前 {result['total']} 个",
                    45,
                )
            half_entries = load_entries()
            self._log(task_id, "正在解析派送订单", 55)
            po_map = build_delivery_sku_map(
                self.file_path(task_id, "delivery"),
                log=lambda message: self._log(task_id, message, 65),
            )
            self._log(task_id, "正在匹配销售订单并计算成本", 75)
            result_path = self._task_dir(task_id) / "销售订单汇总表.xlsx"
            stats = generate_summary(
                self.file_path(task_id, "sales"),
                catalog,
                po_map,
                result_path,
                half_entries,
                log=lambda message: self._log(task_id, message, 95),
            )
            self._update(
                task_id,
                status="completed",
                progress=100,
                message="处理完成",
                finished_at=now_text(),
                stats=stats,
                result_file=result_path.name,
            )
        except Exception as exc:
            logger.exception("Task failed: %s", task_id)
            self._log(task_id, f"处理失败：{exc}")
            self._update(
                task_id,
                status="failed",
                finished_at=now_text(),
                message=f"处理失败：{exc}",
            )

    def get(self, task_id: str) -> Optional[dict]:
        with self._lock:
            task = self._tasks.get(task_id)
            return self.public(task) if task else None

    def list(self, limit: int = 30):
        with self._lock:
            tasks = sorted(
                self._tasks.values(), key=lambda item: item["created_at"], reverse=True
            )[: min(limit, TASK_HISTORY_LIMIT)]
            return [self.public(task) for task in tasks]

    def result_path(self, task_id: str) -> Optional[Path]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task.get("status") != "completed" or not task.get("result_file"):
                return None
            path = self._task_dir(task_id) / task["result_file"]
            return path if path.exists() else None

    @staticmethod
    def public(task: dict) -> dict:
        return {
            "id": task["id"],
            "status": task["status"],
            "progress": task.get("progress", 0),
            "message": task.get("message", ""),
            "created_at": task["created_at"],
            "started_at": task.get("started_at"),
            "finished_at": task.get("finished_at"),
            "stats": task.get("stats", {}),
            "logs": task.get("logs", []),
            "download_ready": bool(task.get("result_file") and task.get("status") == "completed"),
        }


task_manager = TaskManager()
