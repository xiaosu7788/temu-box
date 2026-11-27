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

from sqlalchemy import or_, BigInteger, Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint, create_engine, func, inspect as sa_inspect, select, text, true as sa_true, false as sa_false
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
    # 所属库存类目（A/B...；历史数据归 A）
    category: Mapped[str] = mapped_column(String(16), default="A", server_default="A", index=True)
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
    category: Mapped[str] = mapped_column(String(16), primary_key=True, default="A", server_default="A", index=True)
    # 手动组合：订单成本直接用库存货值（不加头程），导出时货值列显示 /
    manual_combo: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class InventoryExclusion(Base):
    __tablename__ = "inventory_exclusions"
    sku: Mapped[str] = mapped_column(String(255), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class InventoryCategoryDef(Base):
    """库存类目定义（管理员可新建）；A/B 为内置，新类目默认自动探测模式。"""
    __tablename__ = "inventory_category_defs"
    key: Mapped[str] = mapped_column(String(16), primary_key=True)
    label: Mapped[str] = mapped_column(String(80))
    # 提取模式：auto（自动探测）| mapped（固定 sheet 列映射，暂只有内置 A）
    mode: Mapped[str] = mapped_column(String(16), default="auto", server_default="auto")
    # SKU 编码正则（自动探测模式使用）
    code_pattern: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # 价格上限
    price_max: Mapped[float] = mapped_column(Float, default=100.0, server_default="100")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_true())
    sort_order: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class HalfHeadcostSku(Base):
    __tablename__ = "half_headcost_skus"
    # 品类隔离：同一 SKU 可出现在不同品类的减半名单中
    category_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(255), primary_key=True)
    # 所属库存类目；同一业务品类同一 SKU 可分别存在于 A/B 库存类目
    inventory_category: Mapped[str] = mapped_column(String(16), primary_key=True, default="A", server_default="A", index=True)
    set_type: Mapped[str] = mapped_column(String(64), default="单品")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class WeightHeadcostSku(Base):
    """按克重计算头程的 SKU 名单（头程 = 克重 × 0.017）。"""

    __tablename__ = "weight_headcost_skus"
    category_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(255), primary_key=True)
    inventory_category: Mapped[str] = mapped_column(String(16), primary_key=True, default="A", server_default="A", index=True)
    weight_g: Mapped[float] = mapped_column(Float, default=0)
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
    # 绑定的库存类目（A/B...；NULL = 默认 A）
    inventory_category: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
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
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    role: Mapped[str] = mapped_column(String(20), default="user")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # 店铺管理模块准入（管理员逐个开启；关闭时前端隐藏入口且接口 403）
    store_access: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false(), nullable=False)
    # 一级模块权限（JSON 数组）：orders/inventory/activities/tasks/store
    modules: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    # 店铺管理下的二级模块（JSON 数组）：goods/create/kefu/actions
    store_modules: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    # 品类权限（JSON 数组，存 Category.code）。
    # 空数组 = 全部品类（默认，老用户行为不变）；非空 = 只能操作这些品类。
    categories: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class TemuSession(Base):
    """用户与 Temu 店铺的一对一会话绑定（一用户一店铺，退出即解绑）。"""

    __tablename__ = "temu_sessions"
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 账号原文（用于重新登录与 bridge 调用；密码永不入库，除非用户勾选「记住密码」）
    account: Mapped[str] = mapped_column(String(160), default="")
    # sha1(小写账号) 前 16 位：用于"同一账号全系统只能有一个活跃会话"的唯一校验
    account_hash: Mapped[str] = mapped_column(String(40), index=True, default="")
    account_masked: Mapped[str] = mapped_column(String(120), default="")
    mall_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    mall_name: Mapped[str] = mapped_column(String(120), default="")
    # 登录时抓到的店铺列表（JSON），前端多店铺时用于选择
    mall_list: Mapped[str] = mapped_column(Text, default="[]")
    # logged_out | logging_in | active | invalid | cooldown
    status: Mapped[str] = mapped_column(String(24), default="logged_out", server_default="logged_out")
    remember: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false(), nullable=False)
    login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cooldown_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str] = mapped_column(String(40), default="")
    last_error: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class TemuStoreAccount(Base):
    """用户保存的店铺账号库（可多个）。

    与 `temu_sessions` 的区别：sessions 是「此刻正在操作的唯一店铺」，
    这里是「记住的账号密码清单」，切换店铺时从这里取凭据重新登录。
    """

    __tablename__ = "temu_store_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    account: Mapped[str] = mapped_column(String(160), default="")
    account_hash: Mapped[str] = mapped_column(String(40), index=True, default="")
    account_masked: Mapped[str] = mapped_column(String(120), default="")
    label: Mapped[str] = mapped_column(String(120), default="")
    mall_name: Mapped[str] = mapped_column(String(120), default="")
    ciphertext: Mapped[str] = mapped_column(Text, default="")
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class TemuCredential(Base):
    """勾选「记住密码」时保存的 Temu 账号凭据（AES-GCM 密文，密钥来自 CREDENTIAL_KEY）。"""

    __tablename__ = "temu_credentials"
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account: Mapped[str] = mapped_column(String(160), default="")
    ciphertext: Mapped[str] = mapped_column(Text, default="")
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class TemuLoginLog(Base):
    """店铺登录/退出/失效事件（用户自助排障 + 管理员审计）。"""

    __tablename__ = "temu_login_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    account_masked: Mapped[str] = mapped_column(String(120), default="")
    action: Mapped[str] = mapped_column(String(32), index=True)
    code: Mapped[str] = mapped_column(String(40), default="")
    message: Mapped[str] = mapped_column(String(255), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)


class TemuKefuBatch(Base):
    """客服写操作的批量任务：一批编号、一个工具、一次确认。

    执行在后台线程里串行进行（每个编号 = 查商品 + 拦截检查 + 提交），
    前端轮询本表与关联的 temu_kefu_actions 取进度。
    """

    __tablename__ = "temu_kefu_batches"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    account_masked: Mapped[str] = mapped_column(String(120), default="")
    tool_id: Mapped[str] = mapped_column(String(32), default="")
    tool_name: Mapped[str] = mapped_column(String(80), default="")
    data_type_name: Mapped[str] = mapped_column(String(8), default="SKC")
    reason_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reason_detail: Mapped[str] = mapped_column(String(255), default="")
    # queued | running | done | cancelled | failed
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class TemuKefuAction(Base):
    """客服写操作的事务记录：预览（preview）→ 确认（sent）→ 回执（replied）。

    核心不变量：**拦截检查（preIntercept）有状态，同一商品只能成功一次**。
    所以创建时一次性完成 queryProduct + preIntercept + 组装 content，全部落库；
    确认阶段只发送已存的 request_body，绝不重新拦截。
    """

    __tablename__ = "temu_kefu_actions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    # 批量执行的归属（单条操作为 NULL）
    batch_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    account_masked: Mapped[str] = mapped_column(String(120), default="")
    tool_id: Mapped[str] = mapped_column(String(32), default="")
    tool_name: Mapped[str] = mapped_column(String(80), default="")
    # 1=SKC 2=SKU 5=SPU
    data_type: Mapped[int] = mapped_column(Integer, default=1)
    data_type_name: Mapped[str] = mapped_column(String(8), default="SKC")
    data_id: Mapped[str] = mapped_column(String(64), default="")
    reason_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reason_detail: Mapped[str] = mapped_column(String(255), default="")
    # preview | sent | accepted | replied | failed | rejected | retry_pending | unknown
    stage: Mapped[str] = mapped_column(String(20), default="preview", index=True)
    preview: Mapped[str] = mapped_column(Text, default="")
    product_snapshot: Mapped[str] = mapped_column(Text, default="")
    parent_msg_id: Mapped[str] = mapped_column(String(32), default="")
    intercept_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    intercept_message: Mapped[str] = mapped_column(String(255), default="")
    request_body: Mapped[str] = mapped_column(Text, default="")
    response: Mapped[str] = mapped_column(Text, default="")
    msg_id: Mapped[str] = mapped_column(String(32), default="")
    robot_reply: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # 失败自动重试（平台「系统忙碌」属于临时错误）
    retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


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
            "single_rules": [{"mode": "last_segment", "delimiter": "-"}],
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
    - inventory_versions / inventory_items / half_headcost_skus 补类目列（历史数据归 A）
    """
    with engine.begin() as connection:
        inspector = sa_inspect(connection)
        if not inspector.has_table("categories"):
            Category.__table__.create(connection)
        else:
            # 旧库补列（allowed_regions：NULL = 全部区域开放；inventory_category：绑定的库存类目）
            category_columns = {column["name"] for column in inspector.get_columns("categories")}
            if "allowed_regions" not in category_columns:
                connection.execute(text("ALTER TABLE categories ADD COLUMN allowed_regions TEXT"))
            if "inventory_category" not in category_columns:
                connection.execute(text("ALTER TABLE categories ADD COLUMN inventory_category VARCHAR(16)"))
        # 库存表补类目列（历史数据归 A）
        for table_name in ("inventory_versions", "inventory_items", "half_headcost_skus"):
            if inspector.has_table(table_name):
                table_columns = {column["name"] for column in inspector.get_columns(table_name)}
                if table_name == "half_headcost_skus":
                    if "inventory_category" not in table_columns:
                        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN inventory_category VARCHAR(16) NOT NULL DEFAULT 'A'"))
                elif "category" not in table_columns:
                    connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN category VARCHAR(16) NOT NULL DEFAULT 'A'"))
        if not inspector.has_table("inventory_category_defs"):
            InventoryCategoryDef.__table__.create(connection)
        if connection.dialect.name == "sqlite" and inspector.has_table("inventory_items"):
            pk_columns = inspector.get_pk_constraint("inventory_items").get("constrained_columns") or []
            if set(pk_columns) != {"sku", "category"}:
                connection.execute(text("ALTER TABLE inventory_items RENAME TO inventory_items_legacy_category"))
                InventoryItem.__table__.create(connection)
                connection.execute(text("INSERT INTO inventory_items (sku, price, set_type, source_sheet, source_row, source_column, inventory_version_id, category, updated_at) SELECT sku, price, set_type, source_sheet, source_row, source_column, inventory_version_id, COALESCE(category, 'A'), updated_at FROM inventory_items_legacy_category"))
                connection.execute(text("DROP TABLE inventory_items_legacy_category"))
        if connection.dialect.name == "sqlite" and inspector.has_table("half_headcost_skus"):
            half_columns = {column["name"] for column in inspector.get_columns("half_headcost_skus")}
            if "category_id" in half_columns:
                half_pk = inspector.get_pk_constraint("half_headcost_skus").get("constrained_columns") or []
                if set(half_pk) != {"category_id", "sku", "inventory_category"}:
                    connection.execute(text("ALTER TABLE half_headcost_skus RENAME TO half_headcost_skus_legacy_inventory"))
                    HalfHeadcostSku.__table__.create(connection)
                    connection.execute(text("INSERT INTO half_headcost_skus (category_id, sku, inventory_category, set_type, updated_at) SELECT category_id, sku, COALESCE(inventory_category, 'A'), set_type, updated_at FROM half_headcost_skus_legacy_inventory"))
                    connection.execute(text("DROP TABLE half_headcost_skus_legacy_inventory"))
        # 品类种子（默认 A 品类）
        if not connection.execute(text("SELECT 1 FROM categories LIMIT 1")).scalar():
            connection.execute(text(
                "INSERT INTO categories (code, name, template_type, set_types, enabled, is_default, sort_order) "
                "VALUES ('A', 'A品类', 'set_based', '[]', true, true, 10)"
            ))
        # 库存类目定义种子（A/B 内置）
        if not connection.execute(text("SELECT 1 FROM inventory_category_defs LIMIT 1")).scalar():
            connection.execute(text(
                "INSERT INTO inventory_category_defs (key, label, mode, price_max, enabled, sort_order, created_at, updated_at) "
                "VALUES ('A', 'A类目', 'mapped', 100, true, 10, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('B', 'B类目', 'auto', 100, true, 20, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
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



def _ensure_user_schema() -> None:
    """为本地 create_all 环境中的旧 SQLite users 表补列；生产由 Alembic 迁移负责。"""
    with engine.begin() as connection:
        inspector = sa_inspect(connection)
        if not inspector.has_table("users"):
            return
        columns = {column["name"] for column in inspector.get_columns("users")}
        if "note" not in columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN note TEXT NOT NULL DEFAULT ''"))
        if "store_access" not in columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN store_access BOOLEAN NOT NULL DEFAULT 0"))
        if "modules" not in columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN modules TEXT NOT NULL DEFAULT '[]'"))
        if "store_modules" not in columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN store_modules TEXT NOT NULL DEFAULT '[]'"))


def _ensure_template_schema() -> None:
    """为 product_form_templates 补 account_hash（模板与店铺绑定）。"""
    with engine.begin() as connection:
        inspector = sa_inspect(connection)
        if not inspector.has_table("product_form_templates"):
            return
        columns = {c["name"] for c in inspector.get_columns("product_form_templates")}
        if "account_hash" not in columns:
            connection.execute(text("ALTER TABLE product_form_templates ADD COLUMN account_hash VARCHAR(40) NOT NULL DEFAULT ''"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_product_form_templates_account_hash ON product_form_templates (account_hash)"))


def _ensure_temu_schema() -> None:
    """为本地 create_all 环境的旧 SQLite 补列；生产由 Alembic 负责。"""
    with engine.begin() as connection:
        inspector = sa_inspect(connection)
        if not inspector.has_table("temu_kefu_actions"):
            return
        columns = {column["name"] for column in inspector.get_columns("temu_kefu_actions")}
        if "batch_id" not in columns:
            connection.execute(text("ALTER TABLE temu_kefu_actions ADD COLUMN batch_id VARCHAR(64)"))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_temu_kefu_actions_batch_id ON temu_kefu_actions (batch_id)"
            ))
        if "retry_count" not in columns:
            connection.execute(text(
                "ALTER TABLE temu_kefu_actions ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0"
            ))
        if "next_retry_at" not in columns:
            connection.execute(text("ALTER TABLE temu_kefu_actions ADD COLUMN next_retry_at DATETIME"))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_temu_kefu_actions_next_retry_at ON temu_kefu_actions (next_retry_at)"
            ))


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
        _ensure_user_schema()
        _ensure_template_schema()
        _ensure_temu_schema()
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


def _category_key(category: Optional[str]) -> str:
    return (category or "A").strip().upper()


def inventory_signature_matches(signature: dict, category: Optional[str] = "A") -> bool:
    key = _category_key(category)
    with db_session() as session:
        version = session.scalar(select(InventoryVersion).where(InventoryVersion.is_current.is_(True), InventoryVersion.category == key).order_by(InventoryVersion.id.desc()))
        return bool(version and version.source_path == signature["path"] and version.file_size == signature["size"] and version.mtime_ns == signature["mtime_ns"] and version.parser_version == signature["parser_version"])


def current_inventory_metadata(category: Optional[str] = "A") -> Optional[dict]:
    key = _category_key(category)
    with db_session() as session:
        version = session.scalar(select(InventoryVersion).where(InventoryVersion.is_current.is_(True), InventoryVersion.category == key).order_by(InventoryVersion.id.desc()))
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


def invalidate_inventory_catalog(category: Optional[str] = None) -> None:
    """category 为 None 时清全部类目版本；否则只清该类目。"""
    with db_session() as session:
        statement = session.query(InventoryVersion)
        if category is not None:
            statement = statement.filter(InventoryVersion.category == _category_key(category))
        statement.update({InventoryVersion.is_current: False})


def get_inventory_catalog(category: Optional[str] = "A") -> dict:
    key = _category_key(category)
    with db_session() as session:
        excluded = set(session.scalars(select(InventoryExclusion.sku)).all())
        rows = session.scalars(select(InventoryItem).where(InventoryItem.category == key)).all()
        return {row.sku: {key2: getattr(row, key2) for key2 in ("sku", "price", "set_type", "source_sheet", "source_row", "source_column", "manual_combo")} for row in rows if row.sku not in excluded}


def _inventory_item_values(row: InventoryItem) -> dict:
    return {key: getattr(row, key) for key in ("sku", "price", "set_type", "source_sheet", "source_row", "source_column", "manual_combo")}


def _update_current_inventory_count(session, category: Optional[str] = None) -> None:
    session.flush()
    statement = select(InventoryVersion).where(InventoryVersion.is_current.is_(True))
    if category is not None:
        statement = statement.where(InventoryVersion.category == _category_key(category))
    version = session.scalar(statement.order_by(InventoryVersion.id.desc()))
    if version:
        count_statement = session.query(InventoryItem)
        if category is not None:
            count_statement = count_statement.filter(InventoryItem.category == _category_key(category))
        version.sku_count = count_statement.count()


def create_inventory_item(sku: str, price: Optional[float], set_type: str, category: Optional[str] = "A", manual_combo: bool = False) -> dict:
    key = _category_key(category)
    with db_session() as session:
        if session.get(InventoryItem, (sku, key)):
            raise ValueError("库存 SKU 已存在")
        session.query(InventoryExclusion).filter(InventoryExclusion.sku == sku).delete()
        row = InventoryItem(sku=sku, price=price, set_type=set_type, source_sheet="手动维护", category=key, manual_combo=bool(manual_combo))
        session.add(row)
        _update_current_inventory_count(session, key)
        session.flush()
        return _inventory_item_values(row)


def update_inventory_item(old_sku: str, sku: str, price: Optional[float], set_type: str, category: Optional[str] = "A", manual_combo: Optional[bool] = None) -> Optional[dict]:
    key = _category_key(category)
    with db_session() as session:
        row = session.get(InventoryItem, (old_sku, key))
        if not row:
            return None
        if sku != old_sku and session.get(InventoryItem, (sku, key)):
            raise ValueError("库存 SKU 已存在")
        previous_combo = bool(row.manual_combo)
        if sku != old_sku:
            session.delete(row)
            session.flush()
            row = InventoryItem(sku=sku, category=key, manual_combo=previous_combo)
            session.add(row)
        row.price = price
        row.set_type = set_type
        row.source_sheet = "手动维护"
        row.source_row = None
        row.source_column = None
        if manual_combo is not None:
            row.manual_combo = bool(manual_combo)
        row.category = key
        row.inventory_version_id = None
        session.query(InventoryExclusion).filter(InventoryExclusion.sku.in_([old_sku, sku])).delete(synchronize_session=False)
        _update_current_inventory_count(session, key)
        session.flush()
        return _inventory_item_values(row)


def delete_inventory_item(sku: str, category: Optional[str] = "A") -> bool:
    key = _category_key(category)
    with db_session() as session:
        row = session.get(InventoryItem, (sku, key))
        if not row:
            return False
        session.delete(row)
        if not session.get(InventoryExclusion, sku):
            session.add(InventoryExclusion(sku=sku))
        _update_current_inventory_count(session, key)
        return True


def save_inventory_catalog(signature: dict, catalog: dict, category: Optional[str] = "A") -> None:
    key = _category_key(category)
    with db_session() as session:
        session.query(InventoryVersion).filter(InventoryVersion.category == key).update({InventoryVersion.is_current: False})
        if key == "A":
            # A 类目沿用历史行为：新表覆盖时清空全部排除名单
            session.query(InventoryExclusion).delete(synchronize_session=False)
        else:
            # 其他类目：只保留 A 类目 SKU 的排除，避免 B 新表误清 A 的删除标记
            a_skus = {row.sku for row in session.query(InventoryItem.sku).filter(InventoryItem.category == "A").all()}
            session.query(InventoryExclusion).filter(~InventoryExclusion.sku.in_(a_skus or {""})).delete(synchronize_session=False)
        version = InventoryVersion(source_path=signature["path"], file_size=signature["size"], mtime_ns=signature["mtime_ns"], parser_version=signature["parser_version"], sku_count=len(catalog), is_current=True, category=key)
        session.add(version)
        session.flush()
        # 删除该类目旧明细（排除名单中的 SKU 保留在别的类目不受影响——排除表是全局按 SKU）
        # 手动组合是管理员手工维护的标记，重新上传 Excel 时按 SKU 保留
        kept_flags = {row.sku: row.manual_combo for row in session.query(InventoryItem.sku, InventoryItem.manual_combo).filter(InventoryItem.category == key).all()}
        session.query(InventoryItem).filter(InventoryItem.category == key).delete(synchronize_session=False)
        for sku, item in catalog.items():
            flag = item.get("manual_combo")
            session.add(InventoryItem(inventory_version_id=version.id, sku=sku, category=key, manual_combo=bool(kept_flags.get(sku)) if flag is None else bool(flag), **{k: item.get(k) for k in ("price", "set_type", "source_sheet", "source_row", "source_column")}))


def get_inventory_category_defs() -> dict:
    with db_session() as session:
        rows = session.query(InventoryCategoryDef).filter(InventoryCategoryDef.enabled.is_(True)).order_by(InventoryCategoryDef.sort_order, InventoryCategoryDef.key).all()
        return {row.key: {"label": row.label, "mode": row.mode, "code_pattern": row.code_pattern, "price_max": row.price_max, "sort_order": row.sort_order} for row in rows}


def list_inventory_category_defs(include_disabled: bool = True) -> list[dict]:
    with db_session() as session:
        rows = session.query(InventoryCategoryDef).order_by(InventoryCategoryDef.sort_order, InventoryCategoryDef.key).all()
        return [{"key": row.key, "label": row.label, "mode": row.mode, "code_pattern": row.code_pattern, "price_max": row.price_max, "enabled": row.enabled, "sort_order": row.sort_order} for row in rows if include_disabled or row.enabled]


def create_inventory_category_def(key: str, label: str, code_pattern: Optional[str], price_max: float, mode: str = "auto") -> dict:
    with db_session() as session:
        if session.get(InventoryCategoryDef, key):
            raise ValueError("库存类目代码已存在")
        row = InventoryCategoryDef(key=key, label=label, mode=mode, code_pattern=code_pattern, price_max=price_max, enabled=True)
        session.add(row)
        session.flush()
        return {"key": row.key, "label": row.label, "mode": row.mode, "code_pattern": row.code_pattern, "price_max": row.price_max, "enabled": row.enabled, "sort_order": row.sort_order}


def update_inventory_category_def(key: str, label: str, code_pattern: Optional[str], price_max: float, enabled: bool, sort_order: int) -> dict:
    with db_session() as session:
        row = session.get(InventoryCategoryDef, key)
        if not row:
            raise ValueError("库存类目不存在")
        if key in ("A", "B") and not enabled:
            raise ValueError("内置库存类目不能停用")
        row.label, row.code_pattern, row.price_max, row.enabled, row.sort_order = label, code_pattern, price_max, enabled, sort_order
        row.updated_at = datetime.now(timezone.utc)
        return {"key": row.key, "label": row.label, "mode": row.mode, "code_pattern": row.code_pattern, "price_max": row.price_max, "enabled": row.enabled, "sort_order": row.sort_order}


def delete_inventory_category_def(key: str) -> None:
    if key in ("A", "B"):
        raise ValueError("内置库存类目不能删除")
    with db_session() as session:
        row = session.get(InventoryCategoryDef, key)
        if not row:
            raise ValueError("库存类目不存在")
        session.delete(row)
def load_half_entries(category_id: int, inventory_category: Optional[str] = "A") -> dict:
    key = (inventory_category or "A").strip().upper()
    with db_session() as session:
        return {row.sku: row.set_type for row in session.scalars(select(HalfHeadcostSku).where(HalfHeadcostSku.category_id == category_id, HalfHeadcostSku.inventory_category == key)).all()}


def count_half_entries(inventory_category: Optional[str] = None) -> int:
    with db_session() as session:
        statement = select(func.count()).select_from(HalfHeadcostSku)
        if inventory_category:
            statement = statement.where(HalfHeadcostSku.inventory_category == (inventory_category or "A").strip().upper())
        return session.scalar(statement) or 0


def merge_half_entries(category_id: int, values: dict, inventory_category: Optional[str] = "A") -> tuple[int, int]:
    key = (inventory_category or "A").strip().upper()
    with db_session() as session:
        before = {row.sku for row in session.scalars(select(HalfHeadcostSku).where(HalfHeadcostSku.category_id == category_id, HalfHeadcostSku.inventory_category == key)).all()}
        for sku, set_type in values.items():
            row = session.get(HalfHeadcostSku, (category_id, sku, key))
            if row:
                row.set_type = set_type
                row.updated_at = datetime.now(timezone.utc)
            else:
                session.add(HalfHeadcostSku(category_id=category_id, sku=sku, inventory_category=key, set_type=set_type))
        return len(set(values) - before), len(before | set(values))


def delete_half_entry(category_id: int, sku: str, inventory_category: Optional[str] = "A") -> bool:
    key = (inventory_category or "A").strip().upper()
    with db_session() as session:
        row = session.get(HalfHeadcostSku, (category_id, sku, key))
        if not row:
            return False
        session.delete(row)
        return True


def upsert_half_entry(category_id: int, sku: str, set_type: str, inventory_category: Optional[str] = "A") -> dict:
    """新增或更新单条头程减半名单（主键：品类 + SKU + 库存类目）。"""
    key = (inventory_category or "A").strip().upper()
    with db_session() as session:
        row = session.get(HalfHeadcostSku, (category_id, sku, key))
        if row:
            row.set_type = set_type
            row.updated_at = datetime.now(timezone.utc)
        else:
            row = HalfHeadcostSku(category_id=category_id, sku=sku, inventory_category=key, set_type=set_type)
            session.add(row)
        session.flush()
        return {"sku": row.sku, "set_type": row.set_type, "category_id": row.category_id, "inventory_category": row.inventory_category}


def load_weight_entries(category_id: int, inventory_category: Optional[str] = "A") -> dict:
    key = (inventory_category or "A").strip().upper()
    with db_session() as session:
        rows = session.scalars(select(WeightHeadcostSku).where(WeightHeadcostSku.category_id == category_id, WeightHeadcostSku.inventory_category == key)).all()
        return {row.sku: float(row.weight_g or 0) for row in rows}


def count_weight_entries(inventory_category: Optional[str] = None) -> int:
    with db_session() as session:
        statement = select(func.count()).select_from(WeightHeadcostSku)
        if inventory_category:
            statement = statement.where(WeightHeadcostSku.inventory_category == (inventory_category or "A").strip().upper())
        return session.scalar(statement) or 0


def merge_weight_entries(category_id: int, values: dict, inventory_category: Optional[str] = "A") -> tuple[int, int]:
    key = (inventory_category or "A").strip().upper()
    with db_session() as session:
        before = {row.sku for row in session.scalars(select(WeightHeadcostSku).where(WeightHeadcostSku.category_id == category_id, WeightHeadcostSku.inventory_category == key)).all()}
        for sku, weight_g in values.items():
            row = session.get(WeightHeadcostSku, (category_id, sku, key))
            if row:
                row.weight_g = float(weight_g)
                row.updated_at = datetime.now(timezone.utc)
            else:
                session.add(WeightHeadcostSku(category_id=category_id, sku=sku, inventory_category=key, weight_g=float(weight_g)))
        return len(set(values) - before), len(before | set(values))


def upsert_weight_entry(category_id: int, sku: str, weight_g: float, inventory_category: Optional[str] = "A") -> dict:
    key = (inventory_category or "A").strip().upper()
    with db_session() as session:
        row = session.get(WeightHeadcostSku, (category_id, sku, key))
        if row:
            row.weight_g = float(weight_g)
            row.updated_at = datetime.now(timezone.utc)
        else:
            row = WeightHeadcostSku(category_id=category_id, sku=sku, inventory_category=key, weight_g=float(weight_g))
            session.add(row)
        session.flush()
        return {"sku": row.sku, "weight_g": float(row.weight_g), "headcost": round(float(row.weight_g) * 0.017, 2), "category_id": row.category_id, "inventory_category": row.inventory_category}


def delete_weight_entry(category_id: int, sku: str, inventory_category: Optional[str] = "A") -> bool:
    key = (inventory_category or "A").strip().upper()
    with db_session() as session:
        row = session.get(WeightHeadcostSku, (category_id, sku, key))
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


def list_audit_logs(
    page: int = 1,
    page_size: int = 30,
    action: str = "",
    actor_id: Optional[int] = None,
    keyword: str = "",
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> dict:
    with db_session() as session:
        statement = select(AuditLog)
        if action:
            statement = statement.where(AuditLog.action == action)
        if actor_id is not None:
            statement = statement.where(AuditLog.actor_id == actor_id)
        if start is not None:
            statement = statement.where(AuditLog.created_at >= start)
        if end is not None:
            statement = statement.where(AuditLog.created_at <= end)
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


def delete_audit_logs(ids: list[int]) -> int:
    """按 id 批量删除审计日志，返回删除条数。"""
    cleaned = [int(x) for x in (ids or []) if str(x).strip().isdigit()]
    if not cleaned:
        return 0
    with db_session() as session:
        rows = session.scalars(select(AuditLog).where(AuditLog.id.in_(cleaned))).all()
        for row in rows:
            session.delete(row)
        return len(rows)


def distinct_audit_actions() -> list[str]:
    with db_session() as session:
        return list(session.scalars(select(AuditLog.action).distinct().order_by(AuditLog.action)).all())


def prune_audit_logs(before: datetime) -> int:
    with db_session() as session:
        return session.query(AuditLog).filter(AuditLog.created_at < before).delete(synchronize_session=False)


def prune_temu_login_logs(before: datetime) -> int:
    """清理过期店铺登录日志（temu_login_logs）。"""
    with db_session() as session:
        return (
            session.query(TemuLoginLog)
            .filter(TemuLoginLog.created_at < before)
            .delete(synchronize_session=False)
        )


def prune_temu_kefu_actions(before: datetime) -> int:
    """清理过期客服写操作审计（temu_kefu_actions）。"""
    with db_session() as session:
        return (
            session.query(TemuKefuAction)
            .filter(TemuKefuAction.created_at < before)
            .delete(synchronize_session=False)
        )


def get_user_by_username(username: str) -> Optional[dict]:
    with db_session() as session:
        row = session.scalar(select(User).where(User.username == username))
        return {**user_dict(row), "password_hash": row.password_hash} if row else None


def get_user(user_id: int) -> Optional[dict]:
    with db_session() as session:
        row = session.get(User, user_id)
        return user_dict(row) if row else None


def _user_timestamp(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    # SQLite strips timezone info on read; persisted application timestamps are UTC.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _load_json_list(raw) -> list:
    """把 TEXT 里存的 JSON 数组读成 list（脏数据一律当空）。"""
    if not raw:
        return []
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    return [str(x) for x in value] if isinstance(value, list) else []


def _dump_json_list(items) -> str:
    cleaned = []
    for x in items or []:
        s = str(x).strip()
        if s and s not in cleaned:
            cleaned.append(s)
    return json.dumps(cleaned, ensure_ascii=False)


def user_dict(row: Optional[User]) -> Optional[dict]:
    if not row:
        return None
    return {
        "id": row.id,
        "username": row.username,
        "display_name": row.display_name,
        "note": row.note or "",
        "role": row.role,
        "status": row.status,
        "store_access": bool(row.store_access),
        "modules": _load_json_list(row.modules),
        "store_modules": _load_json_list(row.store_modules),
        "categories": _load_json_list(getattr(row, "categories", None)),
        "created_at": _user_timestamp(row.created_at),
        "approved_at": _user_timestamp(row.approved_at),
    }


def create_user(username: str, password_hash: str, display_name: str = "", role: str = "user", status: str = "pending", store_access: bool = False, categories: Optional[list] = None) -> dict:
    with db_session() as session:
        row = User(username=username, password_hash=password_hash, display_name=display_name, role=role, status=status, store_access=store_access, categories=json.dumps(list(categories or []), ensure_ascii=False), approved_at=datetime.now(timezone.utc) if status == "approved" else None)
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


def update_user_credentials(
    user_id: int,
    username: Optional[str] = None,
    password_hash: Optional[str] = None,
    display_name: Optional[str] = None,
    note: Optional[str] = None,
    store_access: Optional[bool] = None,
    modules: Optional[list] = None,
    store_modules: Optional[list] = None,
    categories: Optional[list] = None,
) -> Optional[dict]:
    with db_session() as session:
        row = session.get(User, user_id)
        if not row:
            return None
        if username is not None:
            row.username = username
        if password_hash:
            row.password_hash = password_hash
        if display_name is not None:
            row.display_name = display_name
        if note is not None:
            row.note = note
        if categories is not None:
            row.categories = json.dumps(list(categories), ensure_ascii=False)
        if store_access is not None:
            row.store_access = bool(store_access)
        if modules is not None:
            row.modules = _dump_json_list(modules)
            # store_access 与 modules 里的 "store" 保持同步（老代码仍读它）
            row.store_access = "store" in [str(x) for x in modules]
        if store_modules is not None:
            row.store_modules = _dump_json_list(store_modules)
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

def bulk_update_users(user_ids: list[int], *, display_name: Optional[str] = None, note: Optional[str] = None, status: Optional[str] = None, store_access: Optional[bool] = None) -> list[dict]:
    """原子批量更新普通用户资料/状态，禁止混入管理员账号。"""
    ids = list(dict.fromkeys(user_ids))
    if status is not None and status not in {"approved", "rejected"}:
        raise ValueError("批量状态只能设为启用或禁用")
    with db_session() as session:
        rows = session.scalars(select(User).where(User.id.in_(ids))).all() if ids else []
        if len(rows) != len(ids):
            raise ValueError("所选用户中包含不存在的账号")
        if any(row.role == "admin" for row in rows):
            raise ValueError("批量操作不能包含管理员账号")
        for row in rows:
            if display_name is not None:
                row.display_name = display_name
            if note is not None:
                row.note = note
            if status is not None:
                row.status = status
                row.approved_at = datetime.now(timezone.utc) if status == "approved" else None
            if store_access is not None:
                row.store_access = bool(store_access)
        return [user_dict(row) for row in rows]


def bulk_delete_users(user_ids: list[int]) -> list[dict]:
    """原子批量删除普通用户，禁止混入管理员账号。"""
    ids = list(dict.fromkeys(user_ids))
    with db_session() as session:
        rows = session.scalars(select(User).where(User.id.in_(ids))).all() if ids else []
        if len(rows) != len(ids):
            raise ValueError("所选用户中包含不存在的账号")
        if any(row.role == "admin" for row in rows):
            raise ValueError("批量删除不能包含管理员账号")
        removed = [user_dict(row) for row in rows]
        for row in rows:
            session.delete(row)
        return removed


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
            # 管理员始终拥有店铺管理入口，避免把自己锁在门外
            if not row.store_access:
                row.store_access = True
            return user_dict(row)
        row = User(username=username, password_hash=password_hash, display_name="管理员", role="admin", status="approved", store_access=True, approved_at=datetime.now(timezone.utc))
        session.add(row)
        session.flush()
        return user_dict(row)


# ==================== 店铺会话（temu_sessions / 日志 / 凭据） ====================

_TEMU_SESSION_FIELDS = {
    "account", "account_hash", "account_masked", "mall_id", "mall_name", "mall_list",
    "status", "remember", "login_at", "last_seen_at", "expires_at",
    "cooldown_until", "last_error_code", "last_error",
}


def temu_session_dict(row: Optional[TemuSession]) -> Optional[dict]:
    if not row:
        return None
    try:
        mall_list = json.loads(row.mall_list or "[]")
    except (TypeError, json.JSONDecodeError):
        mall_list = []
    return {
        "user_id": row.user_id,
        "account": row.account or "",
        "account_hash": row.account_hash or "",
        "account_masked": row.account_masked or "",
        "mall_id": row.mall_id,
        "mall_name": row.mall_name or "",
        "mall_list": mall_list,
        "status": row.status,
        "remember": bool(row.remember),
        "login_at": _user_timestamp(row.login_at),
        "last_seen_at": _user_timestamp(row.last_seen_at),
        "expires_at": _user_timestamp(row.expires_at),
        "cooldown_until": _user_timestamp(row.cooldown_until),
        "last_error_code": row.last_error_code or "",
        "last_error": row.last_error or "",
        "updated_at": _user_timestamp(row.updated_at),
    }


def get_temu_session(user_id: int) -> Optional[dict]:
    with db_session() as session:
        return temu_session_dict(session.get(TemuSession, user_id))


def upsert_temu_session(user_id: int, **fields) -> dict:
    with db_session() as session:
        row = session.get(TemuSession, user_id)
        if not row:
            row = TemuSession(user_id=user_id)
            session.add(row)
        for key, value in fields.items():
            if key not in _TEMU_SESSION_FIELDS:
                continue
            if key == "mall_list" and not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            setattr(row, key, value)
        row.updated_at = datetime.now(timezone.utc)
        session.flush()
        return temu_session_dict(row)


def clear_temu_session(user_id: int, status: str = "logged_out") -> bool:
    """解绑会话：清账号/店铺信息，但保留「记住密码」偏好。"""
    with db_session() as session:
        row = session.get(TemuSession, user_id)
        if not row:
            return False
        row.status = status
        row.account = ""
        row.account_hash = ""
        row.account_masked = ""
        row.mall_id = None
        row.mall_name = ""
        row.mall_list = "[]"
        row.login_at = None
        row.last_seen_at = None
        row.expires_at = None
        row.updated_at = datetime.now(timezone.utc)
        return True


def find_temu_session_by_account(account_hash: str, exclude_user_id: Optional[int] = None) -> Optional[dict]:
    """查「同一 Temu 账号是否已被别人占用」（占用态包含 cooling，避免互踢）。"""
    if not account_hash:
        return None
    with db_session() as session:
        query = select(TemuSession).where(
            TemuSession.account_hash == account_hash,
            TemuSession.status.in_(("active", "logging_in", "cooldown")),
        )
        if exclude_user_id is not None:
            query = query.where(TemuSession.user_id != exclude_user_id)
        return temu_session_dict(session.scalars(query).first())


def clear_temu_cooldown(user_id: int) -> None:
    """把已到期的 cooldown 状态复位成 logged_out。

    冷却到期后 status 会一直停在 'cooldown'，前端据此永久禁用登录按钮、倒计时显示 0 分 00 秒。
    """
    with db_session() as session:
        row = session.get(TemuSession, user_id)
        if row and row.status == "cooldown":
            row.status = "logged_out"
            row.cooldown_until = None


def list_temu_sessions() -> list[dict]:
    with db_session() as session:
        rows = session.scalars(select(TemuSession).order_by(TemuSession.updated_at.desc())).all()
        return [temu_session_dict(row) for row in rows]


def add_temu_login_log(user_id: Optional[int], account_masked: str, action: str, code: str = "", message: str = "", ip: str = "") -> None:
    with db_session() as session:
        session.add(TemuLoginLog(
            user_id=user_id,
            account_masked=account_masked or "",
            action=action,
            code=str(code or "")[:40],
            message=str(message or "")[:255],
            ip=(ip or "")[:64],
        ))


def delete_temu_login_logs(ids: list[int]) -> int:
    """按 id 批量删除登录日志，返回删除条数。"""
    cleaned = [int(x) for x in (ids or []) if str(x).strip().isdigit()]
    if not cleaned:
        return 0
    with db_session() as session:
        rows = session.scalars(select(TemuLoginLog).where(TemuLoginLog.id.in_(cleaned))).all()
        for row in rows:
            session.delete(row)
        return len(rows)


def list_temu_login_logs(
    limit: int = 100,
    user_id: Optional[int] = None,
    action: Optional[str] = None,
    keyword: str = "",
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> list[dict]:
    with db_session() as session:
        query = select(TemuLoginLog)
        if user_id is not None:
            query = query.where(TemuLoginLog.user_id == user_id)
        if action:
            query = query.where(TemuLoginLog.action == action)
        # 关键字：账号 / 消息 / IP 任一命中
        kw = (keyword or "").strip()
        if kw:
            like = f"%{kw}%"
            query = query.where(
                or_(
                    TemuLoginLog.account_masked.like(like),
                    TemuLoginLog.message.like(like),
                    TemuLoginLog.ip.like(like),
                )
            )
        if start is not None:
            query = query.where(TemuLoginLog.created_at >= start)
        if end is not None:
            query = query.where(TemuLoginLog.created_at <= end)
        query = query.order_by(TemuLoginLog.id.desc()).limit(max(1, min(int(limit), 500)))
        return [
            {
                "id": row.id,
                "user_id": row.user_id,
                "account_masked": row.account_masked,
                "action": row.action,
                "code": row.code,
                "message": row.message,
                "ip": row.ip,
                "created_at": _user_timestamp(row.created_at),
            }
            for row in session.scalars(query).all()
        ]


def _store_account_dict(row: TemuStoreAccount) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "account": row.account or "",
        "account_hash": row.account_hash or "",
        "account_masked": row.account_masked or "",
        "label": row.label or "",
        "mall_name": row.mall_name or "",
        "key_version": row.key_version,
        "created_at": _user_timestamp(row.created_at),
        "updated_at": _user_timestamp(row.updated_at),
        "last_used_at": _user_timestamp(row.last_used_at),
    }


def list_store_accounts(user_id: int) -> list[dict]:
    with db_session() as session:
        rows = session.scalars(
            select(TemuStoreAccount)
            .where(TemuStoreAccount.user_id == user_id)
            .order_by(TemuStoreAccount.updated_at.desc())
        ).all()
        return [_store_account_dict(row) for row in rows]


def get_store_account(account_id: int, user_id: Optional[int] = None) -> Optional[dict]:
    with db_session() as session:
        row = session.get(TemuStoreAccount, account_id)
        if not row or (user_id is not None and row.user_id != user_id):
            return None
        item = _store_account_dict(row)
        item["ciphertext"] = row.ciphertext
        return item


def save_store_account(
    user_id: int,
    *,
    account: str,
    account_hash: str,
    account_masked: str,
    ciphertext: str,
    key_version: int = 1,
    label: str = "",
    mall_name: str = "",
) -> dict:
    """按 (user_id, account_hash) 幂等写入：同一账号再次保存就更新密文。"""
    now = datetime.now(timezone.utc)
    with db_session() as session:
        row = session.scalar(
            select(TemuStoreAccount).where(
                TemuStoreAccount.user_id == user_id,
                TemuStoreAccount.account_hash == account_hash,
            )
        )
        if row is None:
            row = TemuStoreAccount(
                user_id=user_id,
                account=account,
                account_hash=account_hash,
                account_masked=account_masked,
                label=label or "",
                mall_name=mall_name or "",
                ciphertext=ciphertext,
                key_version=key_version,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        else:
            row.account = account
            row.account_masked = account_masked
            row.ciphertext = ciphertext
            row.key_version = key_version
            row.updated_at = now
            if label:
                row.label = label
            if mall_name:
                row.mall_name = mall_name
        session.flush()
        return _store_account_dict(row)


def touch_store_account(account_id: int) -> None:
    with db_session() as session:
        row = session.get(TemuStoreAccount, account_id)
        if row:
            row.last_used_at = datetime.now(timezone.utc)


def delete_store_account(account_id: int, user_id: int) -> bool:
    with db_session() as session:
        row = session.scalar(
            select(TemuStoreAccount).where(
                TemuStoreAccount.id == account_id,
                TemuStoreAccount.user_id == user_id,
            )
        )
        if not row:
            return False
        session.delete(row)
        return True


def get_temu_credential(user_id: int) -> Optional[dict]:
    with db_session() as session:
        row = session.get(TemuCredential, user_id)
        if not row:
            return None
        return {
            "user_id": row.user_id,
            "account": row.account,
            "ciphertext": row.ciphertext,
            "key_version": row.key_version,
            "updated_at": _user_timestamp(row.updated_at),
        }


def save_temu_credential(user_id: int, account: str, ciphertext: str, key_version: int = 1) -> None:
    with db_session() as session:
        row = session.get(TemuCredential, user_id)
        if not row:
            row = TemuCredential(user_id=user_id)
            session.add(row)
        row.account = account or ""
        row.ciphertext = ciphertext or ""
        row.key_version = key_version
        row.updated_at = datetime.now(timezone.utc)


def delete_temu_credential(user_id: int) -> bool:
    with db_session() as session:
        row = session.get(TemuCredential, user_id)
        if not row:
            return False
        session.delete(row)
        return True


# ==================== 客服写操作事务（temu_kefu_actions） ====================

_KEFU_ACTION_FIELDS = {
    "user_id", "batch_id", "account_masked", "tool_id", "tool_name", "data_type", "data_type_name",
    "data_id", "reason_code", "reason_detail", "stage", "preview", "product_snapshot",
    "parent_msg_id", "intercept_code", "intercept_message", "request_body", "response",
    "msg_id", "robot_reply", "error", "sent_at", "finished_at", "retry_count", "next_retry_at",
}
_KEFU_JSON_FIELDS = ("preview", "product_snapshot", "request_body", "response", "robot_reply")


def _json_or_none(value: str):
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def kefu_action_dict(row: Optional[TemuKefuAction]) -> Optional[dict]:
    if not row:
        return None
    data = {
        "id": row.id,
        "user_id": row.user_id,
        "batch_id": row.batch_id,
        "account_masked": row.account_masked,
        "tool_id": row.tool_id,
        "tool_name": row.tool_name,
        "data_type": row.data_type,
        "data_type_name": row.data_type_name,
        "data_id": row.data_id,
        "reason_code": row.reason_code,
        "reason_detail": row.reason_detail,
        "stage": row.stage,
        "parent_msg_id": row.parent_msg_id,
        "intercept_code": row.intercept_code,
        "intercept_message": row.intercept_message,
        "msg_id": row.msg_id,
        "error": row.error,
        "retry_count": row.retry_count or 0,
        "next_retry_at": _user_timestamp(row.next_retry_at),
        "created_at": _user_timestamp(row.created_at),
        "sent_at": _user_timestamp(row.sent_at),
        "finished_at": _user_timestamp(row.finished_at),
    }
    for name in _KEFU_JSON_FIELDS:
        data[name] = _json_or_none(getattr(row, name))
    return data


def create_kefu_action(action_id: str, **fields) -> dict:
    with db_session() as session:
        row = TemuKefuAction(id=action_id)
        for key, value in fields.items():
            if key not in _KEFU_ACTION_FIELDS:
                continue
            if key in _KEFU_JSON_FIELDS and not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            setattr(row, key, value)
        session.add(row)
        session.flush()
        return kefu_action_dict(row)


def get_kefu_action(action_id: str) -> Optional[dict]:
    with db_session() as session:
        return kefu_action_dict(session.get(TemuKefuAction, action_id))


def update_kefu_action(action_id: str, **fields) -> Optional[dict]:
    with db_session() as session:
        row = session.get(TemuKefuAction, action_id)
        if not row:
            return None
        for key, value in fields.items():
            if key not in _KEFU_ACTION_FIELDS:
                continue
            if key in _KEFU_JSON_FIELDS and value is not None and not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            setattr(row, key, value)
        return kefu_action_dict(row)


def list_kefu_actions(
    limit: int = 50,
    user_id: Optional[int] = None,
    stage: Optional[str] = None,
    keyword: str = "",
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> list[dict]:
    with db_session() as session:
        query = select(TemuKefuAction)
        if user_id is not None:
            query = query.where(TemuKefuAction.user_id == user_id)
        if stage:
            query = query.where(TemuKefuAction.stage == stage)
        # 关键字：账号 / 工具名 / 商品 ID / 原因 任一命中
        kw = (keyword or "").strip()
        if kw:
            like = f"%{kw}%"
            query = query.where(
                or_(
                    TemuKefuAction.account_masked.like(like),
                    TemuKefuAction.tool_name.like(like),
                    TemuKefuAction.data_id.like(like),
                    TemuKefuAction.reason_detail.like(like),
                )
            )
        if start is not None:
            query = query.where(TemuKefuAction.created_at >= start)
        if end is not None:
            query = query.where(TemuKefuAction.created_at <= end)
        query = query.order_by(TemuKefuAction.created_at.desc()).limit(max(1, min(int(limit), 200)))
        return [kefu_action_dict(row) for row in session.scalars(query).all()]


def delete_kefu_actions(ids: list[str]) -> int:
    """按 id 批量删除客服操作审计记录，返回删除条数。"""
    cleaned = [str(x).strip() for x in (ids or []) if str(x).strip()]
    if not cleaned:
        return 0
    with db_session() as session:
        rows = session.scalars(select(TemuKefuAction).where(TemuKefuAction.id.in_(cleaned))).all()
        for row in rows:
            session.delete(row)
        return len(rows)


def list_kefu_actions_by_batch(batch_id: str) -> list[dict]:
    """按创建顺序返回一批里的全部操作（进度列表用）。"""
    with db_session() as session:
        rows = session.scalars(
            select(TemuKefuAction)
            .where(TemuKefuAction.batch_id == batch_id)
            .order_by(TemuKefuAction.created_at.asc(), TemuKefuAction.id.asc())
        ).all()
        return [kefu_action_dict(row) for row in rows]


def latest_kefu_sent_at(account_masked: str) -> Optional[datetime]:
    """该（脱敏）账号最近一次成功提交的时间，用于隐形限流保护。"""
    if not account_masked:
        return None
    with db_session() as session:
        row = session.scalars(
            select(TemuKefuAction)
            .where(
                TemuKefuAction.account_masked == account_masked,
                TemuKefuAction.sent_at.isnot(None),
            )
            .order_by(TemuKefuAction.sent_at.desc())
            .limit(1)
        ).first()
        return row.sent_at if row else None


def latest_kefu_parent_msg_id(account_masked: str, tool_id: Optional[str] = None) -> str:
    """该账号最近一次成功提交用过的 parentMsgId。

    工具卡片（contentType=6）只在会话早期推送，随后会被大量提交/受理消息挤出
    「最近 50 条」窗口；而 parentMsgId 本身在会话内可长期复用（实测同一 ID 用了 17 次），
    所以卡片拉不到时用这里兜底，不必让用户重开会话。
    """
    if not account_masked:
        return ""
    with db_session() as session:
        query = select(TemuKefuAction).where(
            TemuKefuAction.account_masked == account_masked,
            TemuKefuAction.parent_msg_id != "",
            TemuKefuAction.stage.in_(("sent", "accepted", "replied")),
        )
        if tool_id:
            query = query.where(TemuKefuAction.tool_id == tool_id)
        query = query.order_by(TemuKefuAction.sent_at.desc()).limit(1)
        row = session.scalars(query).first()
        return (row.parent_msg_id or "") if row else ""


def list_kefu_retry_candidates(now: datetime, limit: int = 10) -> list[dict]:
    """到期待重试的操作（stage=retry_pending 且 next_retry_at <= now）。"""
    with db_session() as session:
        rows = session.scalars(
            select(TemuKefuAction)
            .where(
                TemuKefuAction.stage == "retry_pending",
                TemuKefuAction.next_retry_at.isnot(None),
                TemuKefuAction.next_retry_at <= now,
            )
            .order_by(TemuKefuAction.next_retry_at.asc())
            .limit(max(1, min(int(limit), 50)))
        ).all()
        return [kefu_action_dict(row) for row in rows]


class ProductCreateLog(Base):
    """每次建品的落库记录：用户管理页的「今日/历史创建数」统计来源。

    批量创建时每条商品也各记一行（batch=True），失败也记，error 里是原因。
    """

    __tablename__ = "product_create_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    product_name: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(12), default="ok")  # ok / failed
    error: Mapped[str] = mapped_column(String(500), default="")
    product_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    batch: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ProductBatchState(Base):
    """批量创建任务的进度快照（一用户一条，覆盖式）。

    之前只存在进程内存里，后端一重启进度就没了，前端轮询看到 idle 会以为任务丢了。
    现在每次状态变更写穿到库，重启后能恢复。
    """

    __tablename__ = "product_batch_states"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ProductFormTemplate(Base):
    """手动创建商品的「表单模板」：把一批填好的属性存下来，下次复用。

    复用时刻意跳过易变字段（标题、图片、货号、颜色）——那些每个商品都不一样。
    """

    __tablename__ = "product_form_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    note: Mapped[str] = mapped_column(String(500), default="")
    # 绑定到哪个 Temu 店铺（sha1(account) 前 16 位，与 bridge 同一套哈希）。
    # 空表示「老数据 / 不区分店铺」，对所有店铺可见。
    account_hash: Mapped[str] = mapped_column(String(40), default="", index=True)
    # 存的是 buildForm() 的 JSON 快照
    form_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ==================== 批量任务（temu_kefu_batches） ====================

_KEFU_BATCH_FIELDS = {
    "user_id", "account_masked", "tool_id", "tool_name", "data_type_name",
    "reason_code", "reason_detail", "status", "total", "succeeded", "failed",
    "skipped", "error", "started_at", "finished_at",
}


def kefu_batch_dict(row: Optional[TemuKefuBatch]) -> Optional[dict]:
    if not row:
        return None
    return {
        "id": row.id,
        "user_id": row.user_id,
        "account_masked": row.account_masked,
        "tool_id": row.tool_id,
        "tool_name": row.tool_name,
        "data_type_name": row.data_type_name,
        "reason_code": row.reason_code,
        "reason_detail": row.reason_detail,
        "status": row.status,
        "total": row.total,
        "succeeded": row.succeeded,
        "failed": row.failed,
        "skipped": row.skipped,
        "error": row.error,
        "created_at": _user_timestamp(row.created_at),
        "started_at": _user_timestamp(row.started_at),
        "finished_at": _user_timestamp(row.finished_at),
    }


def create_kefu_batch(batch_id: str, **fields) -> dict:
    with db_session() as session:
        row = TemuKefuBatch(id=batch_id)
        for key, value in fields.items():
            if key in _KEFU_BATCH_FIELDS:
                setattr(row, key, value)
        session.add(row)
        session.flush()
        return kefu_batch_dict(row)


def update_kefu_batch(batch_id: str, **fields) -> Optional[dict]:
    with db_session() as session:
        row = session.get(TemuKefuBatch, batch_id)
        if not row:
            return None
        for key, value in fields.items():
            if key in _KEFU_BATCH_FIELDS:
                setattr(row, key, value)
        return kefu_batch_dict(row)


def get_kefu_batch(batch_id: str) -> Optional[dict]:
    with db_session() as session:
        return kefu_batch_dict(session.get(TemuKefuBatch, batch_id))


def list_kefu_batches(user_id: Optional[int] = None, limit: int = 20) -> list[dict]:
    with db_session() as session:
        query = select(TemuKefuBatch)
        if user_id is not None:
            query = query.where(TemuKefuBatch.user_id == user_id)
        query = query.order_by(TemuKefuBatch.created_at.desc()).limit(max(1, min(int(limit), 100)))
        return [kefu_batch_dict(row) for row in session.scalars(query).all()]


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


def get_flag(key: str) -> bool:
    """读取布尔标记（存于 app_settings，值为 JSON true/false）。"""
    with db_session() as session:
        row = session.get(AppSetting, key)
        if not row:
            return False
        try:
            return bool(json.loads(row.value))
        except (json.JSONDecodeError, TypeError):
            return False


def set_flag(key: str, value: bool = True) -> None:
    """写入布尔标记。用于记录「一次性初始化是否已完成」这类状态。"""
    with db_session() as session:
        row = session.get(AppSetting, key)
        encoded = json.dumps(bool(value))
        if row:
            row.value = encoded
            row.updated_at = datetime.now(timezone.utc)
        else:
            session.add(AppSetting(key=key, value=encoded))

def list_product_create_logs(
    user_id: int,
    *,
    status: str = "",
    keyword: str = "",
    page: int = 1,
    size: int = 20,
) -> dict:
    """当前用户的建品记录（操作记录页「商品创建」用）。

    status 可选 ok / failed；keyword 匹配商品名。
    """
    from sqlalchemy import func, select

    conds = [ProductCreateLog.user_id == int(user_id)]
    if status in ("ok", "failed"):
        conds.append(ProductCreateLog.status == status)
    kw = (keyword or "").strip()
    if kw:
        conds.append(ProductCreateLog.product_name.ilike(f"%{kw}%"))

    page = max(1, int(page or 1))
    size = min(100, max(1, int(size or 20)))
    with db_session() as session:
        total = session.scalar(select(func.count()).select_from(ProductCreateLog).where(*conds)) or 0
        rows = session.scalars(
            select(ProductCreateLog)
            .where(*conds)
            .order_by(ProductCreateLog.created_at.desc(), ProductCreateLog.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        ).all()
        ok_count = session.scalar(
            select(func.count()).select_from(ProductCreateLog).where(*conds, ProductCreateLog.status == "ok")
        ) or 0
        items = [
            {
                "id": r.id,
                "product_name": r.product_name,
                "status": r.status,
                "error": r.error or "",
                "product_id": r.product_id,
                "batch": bool(r.batch),
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ]
    return {
        "items": items,
        "total": int(total),
        "ok": int(ok_count),
        "failed": int(total) - int(ok_count),
        "page": page,
        "size": size,
    }


def log_product_create(
    user_id: int,
    product_name: str,
    *,
    status: str = "ok",
    error: str = "",
    product_id: Optional[int] = None,
    batch: bool = False,
) -> None:
    """记一条建品日志（失败不抛出，统计用途不能反过来影响建品）。"""
    try:
        with db_session() as session:
            session.add(
                ProductCreateLog(
                    user_id=int(user_id),
                    product_name=str(product_name or "")[:255],
                    status=str(status or "ok")[:12],
                    error=str(error or "")[:500],
                    product_id=product_id,
                    batch=bool(batch),
                )
            )
    except Exception:  # noqa: BLE001 - 日志失败不影响主流程
        logging.getLogger("temubox.product").warning("写建品日志失败", exc_info=True)


def product_create_stats(user_id: Optional[int] = None) -> dict:
    """建品统计：总成功数、今日成功数、失败数。user_id=None 时统计全站。"""
    from datetime import timedelta

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    with db_session() as session:
        def _count(*conds):
            stmt = select(func.count()).select_from(ProductCreateLog)
            for c in conds:
                stmt = stmt.where(c)
            return session.scalar(stmt) or 0

        base = [] if user_id is None else [ProductCreateLog.user_id == int(user_id)]
        ok = [ProductCreateLog.status == "ok"]
        return {
            "total_ok": _count(*(base + ok)),
            "today_ok": _count(*(base + ok + [ProductCreateLog.created_at >= today])),
            "week_ok": _count(*(base + ok + [ProductCreateLog.created_at >= now - timedelta(days=7)])),
            "failed": _count(*(base + [ProductCreateLog.status == "failed"])),
        }

def save_batch_state(user_id: int, payload: dict) -> None:
    """写穿批量任务进度（失败只记日志，不能反过来影响建品）。"""
    try:
        with db_session() as session:
            row = session.get(ProductBatchState, int(user_id))
            encoded = json.dumps(payload, ensure_ascii=False)
            if row:
                row.payload = encoded
                row.updated_at = datetime.now(timezone.utc)
            else:
                session.add(ProductBatchState(user_id=int(user_id), payload=encoded))
    except Exception:  # noqa: BLE001
        logging.getLogger("temubox.product").warning("写批量进度失败", exc_info=True)


def load_batch_state(user_id: int) -> Optional[dict]:
    """读回批量任务进度（没有返回 None）。"""
    try:
        with db_session() as session:
            row = session.get(ProductBatchState, int(user_id))
            if not row or not row.payload:
                return None
            data = json.loads(row.payload)
            return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None
