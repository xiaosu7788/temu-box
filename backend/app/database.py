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

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint, create_engine, func, inspect as sa_inspect, select, text, true as sa_true, false as sa_false
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import DATA_DIR, DATABASE_URL, DB_CONNECT_TIMEOUT, DB_STATEMENT_TIMEOUT_MS, HALF_HEADCOST_PATH, PRICE_CACHE_PATH, TASKS_DIR

logger = logging.getLogger("temubox.database")
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
    # 品类隔离：同一 SKU 可出现在不同品类的减半名单中
    category_id: Mapped[int] = mapped_column(Integer, primary_key=True)
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


class Region(Base):
    __tablename__ = "regions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    # set_based（套装型）| no_set（无套装型）| custom_set（自定义套装型）
    template_type: Mapped[str] = mapped_column(String(20), default="set_based")
    # custom_set 品类的自定义套装档位（JSON 数组，如 ["4","6","8"]）
    set_types: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 品类开放的区域（JSON 数组存区域代码）；NULL = 全部区域开放
    allowed_regions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_true())
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false())
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=func.now())


class RegionConfig(Base):
    __tablename__ = "region_configs"
    __table_args__ = (UniqueConstraint("region_id", "category_id", "module", name="uq_region_config_category_module"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    region_id: Mapped[int] = mapped_column(Integer, index=True)
    category_id: Mapped[int] = mapped_column(Integer, index=True)
    module: Mapped[str] = mapped_column(String(20))
    strategy: Mapped[str] = mapped_column(String(64))
    config_json: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
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
    region_code: Mapped[str] = mapped_column(String(16), default="US")
    region_name: Mapped[str] = mapped_column(String(80), default="美国区")
    category_code: Mapped[str] = mapped_column(String(16), default="A")
    category_name: Mapped[str] = mapped_column(String(80), default="A品类")
    config_version: Mapped[int] = mapped_column(Integer, default=1)
    config_snapshot: Mapped[str] = mapped_column(Text, default="{}")
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


class AuditLog(Base):
    __tablename__ = "audit_logs"
    # 注：SQLite 的 BIGINT 主键不会自增，必须用 INTEGER
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    actor_username: Mapped[str] = mapped_column(String(80), default="")
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str] = mapped_column(String(32), default="")
    target_id: Mapped[str] = mapped_column(String(128), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)


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
        "id_profit_rules": [],
        "default_skc_rules": {
            "set_keywords": ["piece", "件套", "套装"],
            "set_mappings": [],
            "single_mode": "last_segment",
            "single_delimiter": "-",
            "single_marker": "price",
        },
    },
}


def _json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def _json_value(value: str, default):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _merge_dict(default: dict, value: dict) -> dict:
    merged = deepcopy(default)
    for key, item in value.items():
        if isinstance(item, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], item)
        else:
            merged[key] = item
    return merged


def _ensure_category_schema() -> None:
    """为旧库补齐品类维度（本地 create_all 环境的轻量迁移）。

    与 Alembic 迁移 20260908_01 等价：
    - categories 表种子（默认 A 品类，套装型）
    - region_configs 增加 category_id 并重建唯一约束
    - half_headcost_skus 重建为 (category_id, sku) 复合主键
    - activity_jobs 补齐 category_code / category_name 列（存量归入默认品类）
    """
    with engine.begin() as connection:
        inspector = sa_inspect(connection)
        if not inspector.has_table("categories"):
            Category.__table__.create(connection)
        else:
            # 旧库补列（allowed_regions：NULL = 全部区域开放）
            category_columns = {column["name"] for column in inspector.get_columns("categories")}
            if "allowed_regions" not in category_columns:
                connection.execute(text("ALTER TABLE categories ADD COLUMN allowed_regions TEXT"))
        if not connection.execute(text("SELECT 1 FROM categories LIMIT 1")).scalar():
            connection.execute(text(
                "INSERT INTO categories (code, name, template_type, set_types, enabled, is_default, sort_order) "
                "VALUES ('A', 'A品类', 'set_based', '[]', true, true, 10)"
            ))
        category = connection.execute(text("SELECT id, code, name FROM categories WHERE is_default LIMIT 1")).first()
        category_id = category[0] if category else None
        category_code = str(category[1]) if category else "A"
        category_name = str(category[2]) if category else "A品类"

        if inspector.has_table("region_configs"):
            columns = {column["name"] for column in inspector.get_columns("region_configs")}
            if "category_id" not in columns:
                # SQLite rename 后旧索引名保留，先删除以释放命名空间（主键 autoindex 跟随表名，无需处理）
                connection.execute(text("DROP INDEX IF EXISTS ix_region_configs_region_id"))
                connection.execute(text("ALTER TABLE region_configs RENAME TO region_configs_legacy"))
                RegionConfig.__table__.create(connection)
                connection.execute(text(
                    "INSERT INTO region_configs (region_id, category_id, module, strategy, config_json, version, updated_by, updated_at) "
                    "SELECT region_id, :cid, module, strategy, config_json, version, updated_by, updated_at FROM region_configs_legacy"
                ), {"cid": category_id})
                connection.execute(text("DROP TABLE region_configs_legacy"))
            else:
                connection.execute(text("UPDATE region_configs SET category_id = :cid WHERE category_id IS NULL"), {"cid": category_id})

        if inspector.has_table("half_headcost_skus"):
            columns = {column["name"] for column in inspector.get_columns("half_headcost_skus")}
            if "category_id" not in columns:
                connection.execute(text("ALTER TABLE half_headcost_skus RENAME TO half_headcost_skus_legacy"))
                HalfHeadcostSku.__table__.create(connection)
                connection.execute(text(
                    "INSERT INTO half_headcost_skus (category_id, sku, set_type, updated_at) "
                    "SELECT :cid, sku, COALESCE(set_type, '单品'), COALESCE(updated_at, CURRENT_TIMESTAMP) FROM half_headcost_skus_legacy"
                ), {"cid": category_id})
                connection.execute(text("DROP TABLE half_headcost_skus_legacy"))

        if inspector.has_table("activity_jobs"):
            columns = {column["name"] for column in inspector.get_columns("activity_jobs")}
            # SQLite 的 ADD COLUMN DEFAULT 只接受字面量，绑定参数会报语法错误
            safe_code = category_code.replace("'", "''")
            safe_name = category_name.replace("'", "''")
            if "category_code" not in columns:
                connection.execute(text(f"ALTER TABLE activity_jobs ADD COLUMN category_code VARCHAR(16) NOT NULL DEFAULT '{safe_code}'"))
            if "category_name" not in columns:
                connection.execute(text(f"ALTER TABLE activity_jobs ADD COLUMN category_name VARCHAR(80) NOT NULL DEFAULT '{safe_name}'"))


def init_database() -> None:
    """Schema ownership:
    - Production (Docker) sets TEMUBOX_AUTO_CREATE_TABLES=0: Alembic owns the
      schema exclusively (`alembic upgrade head` runs before the API starts).
    - Local dev / tests keep the default (create_all) so a fresh SQLite file
      works without running Alembic.
    The legacy JSON seeding below is idempotent and only fills empty tables.
    """
    if os.environ.get("TEMUBOX_AUTO_CREATE_TABLES", "1") != "0":
        Base.metadata.create_all(engine)
        _ensure_category_schema()
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
            if values:
                default_category_id = session.scalar(select(Category.id).where(Category.is_default.is_(True)).order_by(Category.sort_order, Category.id))
                for sku, set_type in values.items():
                    session.add(HalfHeadcostSku(category_id=default_category_id, sku=str(sku), set_type=str(set_type)))
        if session.scalar(select(TaskRecord.id).limit(1)) is None:
            for metadata_path in TASKS_DIR.glob("*/task.json"):
                payload = _json(metadata_path, None)
                if isinstance(payload, dict) and payload.get("id"):
                    session.add(TaskRecord(id=payload["id"], status=payload.get("status", "unknown"), created_at=payload.get("created_at", ""), payload=json.dumps(payload, ensure_ascii=False)))
        if session.scalar(select(AppSetting.key).limit(1)) is None:
            for key, value in DEFAULT_SETTINGS.items():
                session.add(AppSetting(key=key, value=json.dumps(value, ensure_ascii=False)))
        if session.get(AppSetting, "activity_skc_rules") is None:
            activity_row = session.get(AppSetting, "activity")
            activity_settings = _json_value(activity_row.value, {}) if activity_row else {}
            rules = activity_settings.get("default_skc_rules", DEFAULT_SETTINGS["activity"]["default_skc_rules"])
            session.add(AppSetting(key="activity_skc_rules", value=json.dumps(rules, ensure_ascii=False)))
        if session.scalar(select(Region.id).limit(1)) is None:
            persisted = deepcopy(DEFAULT_SETTINGS)
            for row in session.scalars(select(AppSetting)).all():
                try:
                    value = json.loads(row.value)
                    if isinstance(value, dict) and row.key in persisted:
                        persisted[row.key] = _merge_dict(persisted[row.key], value)
                except json.JSONDecodeError:
                    pass
            region = Region(code="US", name="美国区", currency="CNY", enabled=True, is_default=True, sort_order=10)
            session.add(region)
            session.flush()
            default_category_id = session.scalar(select(Category.id).where(Category.is_default.is_(True)).order_by(Category.sort_order, Category.id))
            session.add_all([
                RegionConfig(region_id=region.id, category_id=default_category_id, module="order", strategy="standard_order_v1", config_json=json.dumps(persisted["order"], ensure_ascii=False), version=1),
                RegionConfig(region_id=region.id, category_id=default_category_id, module="activity", strategy="standard_activity_v1", config_json=json.dumps(persisted["activity"], ensure_ascii=False), version=1),
            ])
    seed_category_skc_rules()


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


def _inventory_item_values(row: InventoryItem) -> dict:
    return {key: getattr(row, key) for key in ("sku", "price", "set_type", "source_sheet", "source_row", "source_column")}


def _update_current_inventory_count(session) -> None:
    session.flush()
    version = session.scalar(select(InventoryVersion).where(InventoryVersion.is_current.is_(True)).order_by(InventoryVersion.id.desc()))
    if version:
        version.sku_count = session.query(InventoryItem).count()


def create_inventory_item(sku: str, price: Optional[float], set_type: str) -> dict:
    with db_session() as session:
        if session.get(InventoryItem, sku):
            raise ValueError("库存 SKU 已存在")
        session.query(InventoryExclusion).filter(InventoryExclusion.sku == sku).delete()
        row = InventoryItem(sku=sku, price=price, set_type=set_type, source_sheet="手动维护")
        session.add(row)
        _update_current_inventory_count(session)
        session.flush()
        return _inventory_item_values(row)


def update_inventory_item(old_sku: str, sku: str, price: Optional[float], set_type: str) -> Optional[dict]:
    with db_session() as session:
        row = session.get(InventoryItem, old_sku)
        if not row:
            return None
        if sku != old_sku and session.get(InventoryItem, sku):
            raise ValueError("库存 SKU 已存在")
        if sku != old_sku:
            session.delete(row)
            session.flush()
            row = InventoryItem(sku=sku)
            session.add(row)
        row.price = price
        row.set_type = set_type
        row.source_sheet = "手动维护"
        row.source_row = None
        row.source_column = None
        row.inventory_version_id = None
        session.query(InventoryExclusion).filter(InventoryExclusion.sku.in_([old_sku, sku])).delete(synchronize_session=False)
        _update_current_inventory_count(session)
        session.flush()
        return _inventory_item_values(row)


def delete_inventory_item(sku: str) -> bool:
    with db_session() as session:
        row = session.get(InventoryItem, sku)
        if not row:
            return False
        session.delete(row)
        if not session.get(InventoryExclusion, sku):
            session.add(InventoryExclusion(sku=sku))
        _update_current_inventory_count(session)
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


def load_half_entries(category_id: int) -> dict:
    with db_session() as session:
        return {row.sku: row.set_type for row in session.scalars(select(HalfHeadcostSku).where(HalfHeadcostSku.category_id == category_id)).all()}


def count_half_entries() -> int:
    with db_session() as session:
        return session.scalar(select(func.count()).select_from(HalfHeadcostSku)) or 0


def merge_half_entries(category_id: int, values: dict) -> tuple[int, int]:
    with db_session() as session:
        before = {row.sku for row in session.scalars(select(HalfHeadcostSku).where(HalfHeadcostSku.category_id == category_id)).all()}
        for sku, set_type in values.items():
            row = session.get(HalfHeadcostSku, (category_id, sku))
            if row:
                row.set_type = set_type
                row.updated_at = datetime.now(timezone.utc)
            else:
                session.add(HalfHeadcostSku(category_id=category_id, sku=sku, set_type=set_type))
        return len(set(values) - before), len(before | set(values))


def delete_half_entry(category_id: int, sku: str) -> bool:
    with db_session() as session:
        row = session.get(HalfHeadcostSku, (category_id, sku))
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
    try:
        config_snapshot = json.loads(row.config_snapshot or "{}")
    except json.JSONDecodeError:
        config_snapshot = {}
    return {"id": row.id, "owner_id": row.owner_id, "status": row.status, "filename": row.filename, "output_path": row.output_path, "progress": row.progress, "message": row.message, "logs": logs, "stats": stats, "region_code": row.region_code, "region_name": row.region_name, "category_code": row.category_code, "category_name": row.category_name, "config_version": row.config_version, "config_snapshot": config_snapshot, "created_at": row.created_at.isoformat() if row.created_at else None}


def create_activity_job(job_id: str, filename: str, owner_id: int, snapshot: Optional[dict] = None) -> dict:
    snapshot = snapshot or {}
    region = snapshot.get("region", {})
    category = snapshot.get("category", {})
    versions = snapshot.get("versions", {})
    with db_session() as session:
        row = ActivityJob(
            id=job_id,
            owner_id=owner_id,
            filename=filename,
            status="queued",
            progress=5,
            message="任务已进入处理队列",
            stats="{}",
            logs="[]",
            region_code=str(region.get("code", "US")),
            region_name=str(region.get("name", "美国区")),
            category_code=str(category.get("code", "A")),
            category_name=str(category.get("name", "A品类")),
            config_version=int(versions.get("activity", 1)),
            config_snapshot=json.dumps(snapshot, ensure_ascii=False),
        )
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


def list_activity_jobs_by_status(statuses: tuple[str, ...]) -> list[dict]:
    with db_session() as session:
        rows = session.scalars(select(ActivityJob).where(ActivityJob.status.in_(statuses)).order_by(ActivityJob.created_at.asc())).all()
        return [activity_dict(row) for row in rows]


def task_status_counts() -> dict[str, int]:
    with db_session() as session:
        rows = session.execute(select(TaskRecord.status, func.count()).group_by(TaskRecord.status)).all()
        return {status: count for status, count in rows}


def activity_job_status_counts() -> dict[str, int]:
    with db_session() as session:
        rows = session.execute(select(ActivityJob.status, func.count()).group_by(ActivityJob.status)).all()
        return {status: count for status, count in rows}


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


def get_activity_skc_rules(category_code: Optional[str] = None) -> dict:
    """读取品类的默认 SKC 识别规则；无品类时回落到旧全局键（兼容历史数据）。"""
    with db_session() as session:
        row = None
        if category_code:
            row = session.get(AppSetting, f"activity_skc_rules:{category_code}")
        if row is None:
            row = session.get(AppSetting, "activity_skc_rules")
        value = _json_value(row.value, {}) if row else {}
        return deepcopy(value if isinstance(value, dict) else DEFAULT_SETTINGS["activity"]["default_skc_rules"])


def save_activity_skc_rules(rules: dict, category_code: Optional[str] = None) -> dict:
    with db_session() as session:
        key = f"activity_skc_rules:{category_code}" if category_code else "activity_skc_rules"
        encoded = json.dumps(rules, ensure_ascii=False)
        row = session.get(AppSetting, key)
        if row:
            row.value = encoded
            row.updated_at = datetime.now(timezone.utc)
        else:
            session.add(AppSetting(key=key, value=encoded))
    return get_activity_skc_rules(category_code)


def seed_category_skc_rules() -> None:
    """把旧全局 SKC 规则复制为默认品类的规则（幂等，仅补空）。"""
    with db_session() as session:
        default_category = session.scalar(select(Category).where(Category.is_default.is_(True)).order_by(Category.sort_order, Category.id))
        if not default_category:
            return
        key = f"activity_skc_rules:{default_category.code}"
        if session.get(AppSetting, key) is None:
            legacy = session.get(AppSetting, "activity_skc_rules")
            value = legacy.value if legacy else json.dumps(DEFAULT_SETTINGS["activity"]["default_skc_rules"], ensure_ascii=False)
            session.add(AppSetting(key=key, value=value))


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


def get_system_settings(defaults: dict) -> dict:
    with db_session() as session:
        row = session.get(AppSetting, "system")
        value = _json_value(row.value, {}) if row else {}
        return _merge_dict(defaults, value if isinstance(value, dict) else {})


def save_system_settings(settings: dict) -> None:
    with db_session() as session:
        encoded = json.dumps(settings, ensure_ascii=False)
        row = session.get(AppSetting, "system")
        if row:
            row.value = encoded
            row.updated_at = datetime.now(timezone.utc)
        else:
            session.add(AppSetting(key="system", value=encoded))


def insert_audit_log(actor_id: Optional[int], actor_username: str, action: str, target_type: str, target_id: str, detail: str, ip: str) -> None:
    with db_session() as session:
        session.add(AuditLog(
            actor_id=actor_id,
            actor_username=(actor_username or "")[:80],
            action=action[:64],
            target_type=(target_type or "")[:32],
            target_id=str(target_id or "")[:128],
            detail=(detail or "")[:4000],
            ip=(ip or "")[:64],
        ))


def list_audit_logs(page: int = 1, page_size: int = 30, action: str = "", actor_id: Optional[int] = None, keyword: str = "") -> dict:
    with db_session() as session:
        statement = select(AuditLog)
        if action:
            statement = statement.where(AuditLog.action == action)
        if actor_id is not None:
            statement = statement.where(AuditLog.actor_id == actor_id)
        if keyword:
            like = f"%{keyword}%"
            statement = statement.where((AuditLog.detail.like(like)) | (AuditLog.target_id.like(like)) | (AuditLog.actor_username.like(like)))
        total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = session.scalars(statement.order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
        items = [{
            "id": row.id,
            "actor_id": row.actor_id,
            "actor_username": row.actor_username,
            "action": row.action,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "detail": row.detail,
            "ip": row.ip,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        } for row in rows]
        return {"total": total, "items": items}


def distinct_audit_actions() -> list[str]:
    with db_session() as session:
        return list(session.scalars(select(AuditLog.action).distinct().order_by(AuditLog.action)).all())


def prune_audit_logs(before: datetime) -> int:
    with db_session() as session:
        return session.query(AuditLog).filter(AuditLog.created_at < before).delete(synchronize_session=False)


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


if os.environ.get("TEMUBOX_SKIP_DB_INIT") != "1":
    init_database()
