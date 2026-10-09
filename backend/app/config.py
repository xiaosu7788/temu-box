from __future__ import annotations

import os
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[2]
API_HOST = os.environ.get("API_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("API_PORT", "8089"))
DATA_DIR = Path(os.environ.get("TEMUBOX_DATA_DIR", PROJECT_DIR / "data")).resolve()
INVENTORY_DIR = DATA_DIR / "inventories"
TASKS_DIR = DATA_DIR / "tasks"
LOGS_DIR = DATA_DIR / "logs"
ACTIVITY_DIR = DATA_DIR / "activities"
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if not DATABASE_URL:
    DATABASE_URL = f"sqlite:///{(DATA_DIR / 'temubox.db').as_posix()}"
DB_CONNECT_TIMEOUT = max(3, int(os.environ.get("DB_CONNECT_TIMEOUT", "10")))
DB_STATEMENT_TIMEOUT_MS = max(1000, int(os.environ.get("DB_STATEMENT_TIMEOUT_MS", "30000")))

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
CLEANUP_INTERVAL_SECONDS = max(600, int(os.environ.get("CLEANUP_INTERVAL_SECONDS", "21600")))
AUTH_SECRET = os.environ.get("AUTH_SECRET", "change-this-secret-in-production")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin").strip()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()
SESSION_COOKIE_NAME = "temubox_session"
SESSION_MAX_AGE = max(3600, int(os.environ.get("SESSION_MAX_AGE", "604800")))
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() in {"1", "true", "yes"}

# 一键热更新：真正的更新动作由 compose ops profile 里的运维边车执行
# （docker/updater/server.py），后端只负责管理员鉴权 + 代理，不引入额外 HTTP 依赖。
UPDATER_URL = os.environ.get("UPDATER_URL", "http://updater:8899").rstrip("/")
UPDATER_TOKEN = os.environ.get("UPDATER_TOKEN", "").strip()
UPDATER_STATUS_TIMEOUT = max(3, int(os.environ.get("UPDATER_STATUS_TIMEOUT", "60")))
UPDATER_ACTION_TIMEOUT = max(3, int(os.environ.get("UPDATER_ACTION_TIMEOUT", "20")))


def ensure_directories() -> None:
    for path in (DATA_DIR, INVENTORY_DIR, TASKS_DIR, LOGS_DIR, ACTIVITY_DIR):
        path.mkdir(parents=True, exist_ok=True)
