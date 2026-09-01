from __future__ import annotations

import os
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[2]
API_HOST = os.environ.get("API_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("API_PORT", "8089"))
DATA_DIR = Path(os.environ.get("SALES_TOOL_DATA_DIR", PROJECT_DIR / "data")).resolve()
INVENTORY_DIR = DATA_DIR / "inventories"
TASKS_DIR = DATA_DIR / "tasks"
LOGS_DIR = DATA_DIR / "logs"
ACTIVITY_DIR = DATA_DIR / "activities"

INVENTORY_PATH = Path(
    os.environ.get("INVENTORY_PATH", INVENTORY_DIR / "库存统计表.xlsx")
).resolve()
PRICE_CACHE_PATH = Path(
    os.environ.get("PRICE_CACHE_PATH", DATA_DIR / "price_cache.json")
).resolve()
HALF_HEADCOST_PATH = Path(
    os.environ.get("HALF_HEADCOST_PATH", DATA_DIR / "half_headcost_skus.json")
).resolve()
HALF_HEADCOST_SEED_PATH = Path(
    os.environ.get("HALF_HEADCOST_SEED_PATH", DATA_DIR / "头程减半初始名单.xlsx")
).resolve()

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", 512 * 1024 * 1024))
TASK_WORKERS = max(1, int(os.environ.get("TASK_WORKERS", "2")))
TASK_HISTORY_LIMIT = max(20, int(os.environ.get("TASK_HISTORY_LIMIT", "100")))


def ensure_directories() -> None:
    for path in (DATA_DIR, INVENTORY_DIR, TASKS_DIR, LOGS_DIR, ACTIVITY_DIR):
        path.mkdir(parents=True, exist_ok=True)
