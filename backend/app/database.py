"""Database access and one-time migration from the original JSON files."""
from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, Text, create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import DATA_DIR, DATABASE_URL, DB_CONNECT_TIMEOUT, DB_STATEMENT_TIMEOUT_MS, HALF_HEADCOST_PATH, PRICE_CACHE_PATH, TASKS_DIR

logger = logging.getLogger("sales_tool.database")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgres://") :]
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgresql://") :]

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {
    "connect_timeout": DB_CONNECT_TIMEOUT,
    "options": f"-c statement_timeout={DB_STATEMENT_TIMEOUT_MS}",
}
engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    pool_timeout=DB_CONNECT_TIMEOUT,
)
DATA_DIR.mkdir(parents=True, exist_ok=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


class InventoryVersion(Base):
    __tablename__ = "inventory_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_path: Mapped[str] = mapped_column(String(1024))
    file_size: Mapped[int] = mapped_column(Integer)
    mtime_ns: Mapped[int] = mapped_column(BigInteger)
    parser_version: Mapped[int] = mapped_column(Integer)
    sku_count: Mapped[int] = mapped_column(Integer, default=0)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class InventoryItem(Base):
    __tablename__ = "inventory_items"
    sku: Mapped[str] = mapped_column(String(255), primary_key=True)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    set_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source_sheet: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_row: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_column: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    inventory_version_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class InventoryExclusion(Base):
    __tablename__ = "inventory_exclusions"
    sku: Mapped[str] = mapped_column(String(255), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class HalfHeadcostSku(Base):
    __tablename__ = "half_headcost_skus"
    sku: Mapped[str] = mapped_column(String(255), primary_key=True)
    set_type: Mapped[str] = mapped_column(String(64), default="单品")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class TaskRecord(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[str] = mapped_column(String(32))
    payload: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ActivityJob(Base):
    __tablename__ = "activity_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="completed")
    filename: Mapped[str] = mapped_column(String(255))
    output_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    stats: Mapped[str] = mapped_column(Text, default="{}")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(String(255), default="")
    logs: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120), default="")
    role: Mapped[str] = mapped_column(String(20), default="user")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


DEFAULT_SETTINGS = {
    "order": {
        "headcost": {"单品": 5, "4件套": 5, "5件套": 5, "6件套": 5, "8件套": 10, "10件套": 10, "12件套": 15},
        "operation_fee": 7,
        "extra_item_fee": 2,
        "tail_fee": 0,
        "shipping_subsidy": 0,
    },
    "activity": {
        "headcost": 5,
        "operation_fee": 7,
        "uplift_limit": 1,
        "set_prices": {"4": 42, "5": 45, "6": 48, "8": 71, "10": 75, "12": 92},
        "single_tiers": [{"min_price": 0, "profit": 0}],
    },
}


def _json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def init_database() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal.begin() as session:
        if session.scalar(select(InventoryItem.sku).limit(1)) is None:
            cache = _json(PRICE_CACHE_PATH, {})
            catalog = cache.get("catalog", {}) if isinstance(cache, dict) else {}
            signature = cache.get("signature", {}) if isinstance(cache, dict) else {}
            if catalog:
                version = InventoryVersion(
                    source_path=str(signature.get("path", "legacy price_cache.json")),
                    file_size=int(signature.get("size", 0)),
                    mtime_ns=int(signature.get("mtime_ns", 0)),
                    parser_version=int(signature.get("parser_version", 0)),
                    sku_count=len(catalog),
                    is_current=True,
                )
                session.add(version)
                session.flush()
                for sku, item in catalog.items():
                    session.add(InventoryItem(inventory_version_id=version.id, sku=sku, **{
                        key: item.get(key) for key in ("price", "set_type", "source_sheet", "source_row", "source_column")
                    }))
        if session.scalar(select(HalfHeadcostSku.sku).limit(1)) is None:
            legacy = _json(HALF_HEADCOST_PATH, {})
            values = legacy.get("sku_types", legacy) if isinstance(legacy, dict) else {}
            for sku, set_type in values.items():
                session.add(HalfHeadcostSku(sku=str(sku), set_type=str(set_type)))
        if session.scalar(select(TaskRecord.id).limit(1)) is None:
            for metadata_path in TASKS_DIR.glob("*/task.json"):
                payload = _json(metadata_path, None)
                if isinstance(payload, dict) and payload.get("id"):
                    session.add(TaskRecord(id=payload["id"], status=payload.get("status", "unknown"), created_at=payload.get("created_at", ""), payload=json.dumps(payload, ensure_ascii=False)))
        if session.scalar(select(AppSetting.key).limit(1)) is None:
            for key, value in DEFAULT_SETTINGS.items():
                session.add(AppSetting(key=key, value=json.dumps(value, ensure_ascii=False)))


@contextmanager
def db_session() -> Iterator:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def inventory_signature_matches(signature: dict) -> bool:
    with db_session() as session:
        version = session.scalar(select(InventoryVersion).where(InventoryVersion.is_current.is_(True)).order_by(InventoryVersion.id.desc()))
        return bool(version and version.source_path == signature["path"] and version.file_size == signature["size"] and version.mtime_ns == signature["mtime_ns"] and version.parser_version == signature["parser_version"])


def current_inventory_metadata() -> Optional[dict]:
    with db_session() as session:
        version = session.scalar(select(InventoryVersion).where(InventoryVersion.is_current.is_(True)).order_by(InventoryVersion.id.desc()))
        if not version:
            return None
        return {
            "path": version.source_path,
            "size": version.file_size,
            "mtime_ns": version.mtime_ns,
            "parser_version": version.parser_version,
            "sku_count": version.sku_count,
            "created_at": version.created_at,
        }


def invalidate_inventory_catalog() -> None:
    with db_session() as session:
        session.query(InventoryVersion).update({InventoryVersion.is_current: False})


def get_inventory_catalog() -> dict:
    with db_session() as session:
        excluded = set(session.scalars(select(InventoryExclusion.sku)).all())
        rows = session.scalars(select(InventoryItem)).all()
        return {row.sku: {key: getattr(row, key) for key in ("sku", "price", "set_type", "source_sheet", "source_row", "source_column")} for row in rows if row.sku not in excluded}


def delete_inventory_item(sku: str) -> bool:
    with db_session() as session:
        row = session.get(InventoryItem, sku)
        if not row:
            return False
        session.delete(row)
        if not session.get(InventoryExclusion, sku):
            session.add(InventoryExclusion(sku=sku))
        version = session.scalar(select(InventoryVersion).where(InventoryVersion.is_current.is_(True)).order_by(InventoryVersion.id.desc()))
        if version:
            version.sku_count = session.query(InventoryItem).count()
        return True


def save_inventory_catalog(signature: dict, catalog: dict) -> None:
    with db_session() as session:
        session.query(InventoryVersion).update({InventoryVersion.is_current: False})
        session.query(InventoryExclusion).delete()
        version = InventoryVersion(source_path=signature["path"], file_size=signature["size"], mtime_ns=signature["mtime_ns"], parser_version=signature["parser_version"], sku_count=len(catalog), is_current=True)
        session.add(version)
        session.flush()
        session.query(InventoryItem).delete()
        for sku, item in catalog.items():
            session.add(InventoryItem(inventory_version_id=version.id, sku=sku, **{key: item.get(key) for key in ("price", "set_type", "source_sheet", "source_row", "source_column")}))


def load_half_entries() -> dict:
    with db_session() as session:
        return {row.sku: row.set_type for row in session.scalars(select(HalfHeadcostSku)).all()}


def merge_half_entries(values: dict) -> tuple[int, int]:
    with db_session() as session:
        before = {row.sku for row in session.scalars(select(HalfHeadcostSku)).all()}
        for sku, set_type in values.items():
            row = session.get(HalfHeadcostSku, sku)
            if row:
                row.set_type = set_type
                row.updated_at = datetime.now(timezone.utc)
            else:
                session.add(HalfHeadcostSku(sku=sku, set_type=set_type))
        return len(set(values) - before), len(before | set(values))


def delete_half_entry(sku: str) -> bool:
    with db_session() as session:
        row = session.get(HalfHeadcostSku, sku)
        if not row:
            return False
        session.delete(row)
        return True


def save_task_record(task: dict) -> None:
    with db_session() as session:
        row = session.get(TaskRecord, task["id"])
        values = {"owner_id": task.get("owner_id"), "status": task.get("status", ""), "created_at": task.get("created_at", ""), "payload": json.dumps(task, ensure_ascii=False)}
        if row:
            for key, value in values.items(): setattr(row, key, value)
            row.updated_at = datetime.now(timezone.utc)
        else:
            session.add(TaskRecord(id=task["id"], **values))


def delete_task_record(task_id: str, owner_id: Optional[int] = None) -> bool:
    with db_session() as session:
        row = session.get(TaskRecord, task_id)
        if not row or (owner_id is not None and row.owner_id != owner_id):
            return False
        session.delete(row)
        return True


def load_task_records() -> list[dict]:
    with db_session() as session:
        result = []
        for row in session.scalars(select(TaskRecord).order_by(TaskRecord.created_at.desc())).all():
            try: result.append(json.loads(row.payload))
            except json.JSONDecodeError: pass
        return result


def save_activity_job(job_id: str, filename: str, output_path: Optional[str], stats: dict, status: str = "completed", owner_id: Optional[int] = None) -> None:
    with db_session() as session:
        row = session.get(ActivityJob, job_id)
        values = {"filename": filename, "output_path": output_path, "stats": json.dumps(stats, ensure_ascii=False), "status": status}
        if owner_id is not None:
            values["owner_id"] = owner_id
        if row:
            for key, value in values.items(): setattr(row, key, value)
        else:
            session.add(ActivityJob(id=job_id, **values))


def activity_owner(job_id: str) -> Optional[int]:
    with db_session() as session:
        row = session.get(ActivityJob, job_id)
        return row.owner_id if row else None


def activity_dict(row: Optional[ActivityJob]) -> Optional[dict]:
    if not row:
        return None
    try:
        stats = json.loads(row.stats or "{}")
    except json.JSONDecodeError:
        stats = {}
    try:
        logs = json.loads(row.logs or "[]")
    except json.JSONDecodeError:
        logs = []
    return {"id": row.id, "owner_id": row.owner_id, "status": row.status, "filename": row.filename, "output_path": row.output_path, "progress": row.progress, "message": row.message, "logs": logs, "stats": stats, "created_at": row.created_at.isoformat() if row.created_at else None}


def create_activity_job(job_id: str, filename: str, owner_id: int) -> dict:
    with db_session() as session:
        row = ActivityJob(id=job_id, owner_id=owner_id, filename=filename, status="queued", progress=5, message="任务已进入处理队列", stats="{}", logs="[]")
        session.add(row)
        session.flush()
        return activity_dict(row)


def get_activity_job(job_id: str, owner_id: Optional[int] = None) -> Optional[dict]:
    with db_session() as session:
        row = session.get(ActivityJob, job_id)
        if not row or (owner_id is not None and row.owner_id != owner_id):
            return None
        return activity_dict(row)


def list_activity_jobs(owner_id: Optional[int], limit: int = 50) -> list[dict]:
    with db_session() as session:
        statement = select(ActivityJob)
        if owner_id is not None:
            statement = statement.where(ActivityJob.owner_id == owner_id)
        rows = session.scalars(statement.order_by(ActivityJob.created_at.desc()).limit(limit)).all()
        return [activity_dict(row) for row in rows]


def list_all_activity_jobs(limit: int = 100) -> list[dict]:
    with db_session() as session:
        rows = session.scalars(select(ActivityJob).order_by(ActivityJob.created_at.desc()).limit(limit)).all()
        return [activity_dict(row) for row in rows]


def delete_activity_job(job_id: str, owner_id: Optional[int]) -> str:
    with db_session() as session:
        row = session.get(ActivityJob, job_id)
        if not row or (owner_id is not None and row.owner_id != owner_id):
            return "not_found"
        if row.status in {"queued", "running"}:
            return "active"
        session.delete(row)
        return "deleted"


def update_activity_job(job_id: str, **values) -> Optional[dict]:
    with db_session() as session:
        row = session.get(ActivityJob, job_id)
        if not row:
            return None
        for key in ("status", "progress", "message", "output_path"):
            if key in values:
                setattr(row, key, values[key])
        if "logs" in values:
            row.logs = json.dumps(values["logs"], ensure_ascii=False)
        if "stats" in values:
            row.stats = json.dumps(values["stats"], ensure_ascii=False)
        return activity_dict(row)


def get_settings() -> dict:
    with db_session() as session:
        # Merge persisted values into every default branch so older or partial
        # records cannot make newly added fields appear empty in the UI.
        settings = deepcopy(DEFAULT_SETTINGS)
        for row in session.scalars(select(AppSetting)).all():
            try:
                value = json.loads(row.value)
                if isinstance(value, dict) and isinstance(settings.get(row.key), dict):
                    settings[row.key] = _merge_dict(settings[row.key], value)
                else:
                    settings[row.key] = value
            except json.JSONDecodeError:
                logger.warning("Invalid setting ignored: %s", row.key)
        return settings


def _merge_dict(default: dict, value: dict) -> dict:
    merged = deepcopy(default)
    for key, item in value.items():
        if isinstance(item, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], item)
        else:
            merged[key] = item
    return merged


def save_settings(settings: dict) -> dict:
    with db_session() as session:
        for key, value in settings.items():
            row = session.get(AppSetting, key)
            encoded = json.dumps(value, ensure_ascii=False)
            if row:
                row.value = encoded
                row.updated_at = datetime.now(timezone.utc)
            else:
                session.add(AppSetting(key=key, value=encoded))
    return get_settings()


def get_user_by_username(username: str) -> Optional[dict]:
    with db_session() as session:
        row = session.scalar(select(User).where(User.username == username))
        return {**user_dict(row), "password_hash": row.password_hash} if row else None


def get_user(user_id: int) -> Optional[dict]:
    with db_session() as session:
        row = session.get(User, user_id)
        return user_dict(row) if row else None


def user_dict(row: Optional[User]) -> Optional[dict]:
    if not row:
        return None
    return {"id": row.id, "username": row.username, "display_name": row.display_name, "role": row.role, "status": row.status, "created_at": row.created_at.isoformat() if row.created_at else None, "approved_at": row.approved_at.isoformat() if row.approved_at else None}


def create_user(username: str, password_hash: str, display_name: str = "", role: str = "user", status: str = "pending") -> dict:
    with db_session() as session:
        row = User(username=username, password_hash=password_hash, display_name=display_name, role=role, status=status, approved_at=datetime.now(timezone.utc) if status == "approved" else None)
        session.add(row)
        session.flush()
        return {**user_dict(row), "password_hash": row.password_hash}


def update_user_status(user_id: int, status: str) -> Optional[dict]:
    with db_session() as session:
        row = session.get(User, user_id)
        if not row:
            return None
        row.status = status
        row.approved_at = datetime.now(timezone.utc) if status == "approved" else None
        return user_dict(row)


def update_user_credentials(user_id: int, username: str, password_hash: Optional[str] = None) -> Optional[dict]:
    with db_session() as session:
        row = session.get(User, user_id)
        if not row:
            return None
        row.username = username
        if password_hash:
            row.password_hash = password_hash
        return user_dict(row)


def delete_user(user_id: int) -> bool:
    with db_session() as session:
        row = session.get(User, user_id)
        if not row:
            return False
        session.delete(row)
        return True


def list_users() -> list[dict]:
    with db_session() as session:
        return [user_dict(row) for row in session.scalars(select(User).order_by(User.id.desc())).all()]


def ensure_admin_user(username: str, password_hash: str) -> Optional[dict]:
    if not username or not password_hash:
        return None
    with db_session() as session:
        row = session.scalar(select(User).where(User.username == username))
        if row:
            if row.role != "admin" or row.status != "approved":
                row.role = "admin"
                row.status = "approved"
                row.approved_at = datetime.now(timezone.utc)
            return user_dict(row)
        row = User(username=username, password_hash=password_hash, display_name="管理员", role="admin", status="approved", approved_at=datetime.now(timezone.utc))
        session.add(row)
        session.flush()
        return user_dict(row)


def database_status() -> dict:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok", "dialect": engine.dialect.name}
    except Exception:
        logger.exception("Database health check failed")
        return {"status": "error", "dialect": engine.dialect.name}


if os.environ.get("SALES_TOOL_SKIP_DB_INIT") != "1":
    init_database()
