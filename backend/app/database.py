"""Database access and one-time migration from the original JSON files."""
from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, Text, create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import DATA_DIR, DATABASE_URL, HALF_HEADCOST_PATH, PRICE_CACHE_PATH, TASKS_DIR

logger = logging.getLogger("sales_tool.database")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgres://") :]
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgresql://") :]

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True,
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


class HalfHeadcostSku(Base):
    __tablename__ = "half_headcost_skus"
    sku: Mapped[str] = mapped_column(String(255), primary_key=True)
    set_type: Mapped[str] = mapped_column(String(64), default="单品")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class TaskRecord(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[str] = mapped_column(String(32))
    payload: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ActivityJob(Base):
    __tablename__ = "activity_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="completed")
    filename: Mapped[str] = mapped_column(String(255))
    output_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    stats: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


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
        rows = session.scalars(select(InventoryItem)).all()
        return {row.sku: {key: getattr(row, key) for key in ("sku", "price", "set_type", "source_sheet", "source_row", "source_column")} for row in rows}


def save_inventory_catalog(signature: dict, catalog: dict) -> None:
    with db_session() as session:
        session.query(InventoryVersion).update({InventoryVersion.is_current: False})
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
        values = {"status": task.get("status", ""), "created_at": task.get("created_at", ""), "payload": json.dumps(task, ensure_ascii=False)}
        if row:
            for key, value in values.items(): setattr(row, key, value)
            row.updated_at = datetime.now(timezone.utc)
        else:
            session.add(TaskRecord(id=task["id"], **values))


def load_task_records() -> list[dict]:
    with db_session() as session:
        result = []
        for row in session.scalars(select(TaskRecord).order_by(TaskRecord.created_at.desc())).all():
            try: result.append(json.loads(row.payload))
            except json.JSONDecodeError: pass
        return result


def save_activity_job(job_id: str, filename: str, output_path: Optional[str], stats: dict, status: str = "completed") -> None:
    with db_session() as session:
        row = session.get(ActivityJob, job_id)
        values = {"filename": filename, "output_path": output_path, "stats": json.dumps(stats, ensure_ascii=False), "status": status}
        if row:
            for key, value in values.items(): setattr(row, key, value)
        else:
            session.add(ActivityJob(id=job_id, **values))


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
