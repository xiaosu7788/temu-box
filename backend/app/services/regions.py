from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import Category, Region, RegionConfig, SessionLocal
from app.services.categories import category_allowed_pieces, category_defaults, category_skc_rules, get_category
from app.services.settings import validate_settings

REGION_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_-]{1,15}$")
ORDER_STRATEGIES = {"standard_order_v1"}
ACTIVITY_STRATEGIES = {"standard_activity_v1"}


def _config_value(row: RegionConfig | None, fallback: dict) -> dict:
    if not row:
        return deepcopy(fallback)
    try:
        value = json.loads(row.config_json)
    except json.JSONDecodeError:
        return deepcopy(fallback)
    return value if isinstance(value, dict) else deepcopy(fallback)


def _region_dict(region: Region) -> dict:
    return {
        "id": region.id,
        "code": region.code,
        "name": region.name,
        "currency": region.currency,
        "enabled": region.enabled,
        "is_default": region.is_default,
        "sort_order": region.sort_order,
    }


def _category_brief(category: dict) -> dict:
    return {key: category.get(key) for key in ("id", "code", "name", "template_type", "template_label", "set_types")}


def _profile(session, region: Region, category: dict) -> dict:
    rows = {row.module: row for row in session.scalars(select(RegionConfig).where(RegionConfig.region_id == region.id, RegionConfig.category_id == category["id"])).all()}
    defaults = category_defaults(category["template_type"], category.get("set_types"))
    order_config = _config_value(rows.get("order"), defaults["order"])
    activity_config = _config_value(rows.get("activity"), defaults["activity"])
    # 默认SKC识别规则按品类存储，不随区域配置保存
    activity_config.pop("default_skc_rules", None)
    try:
        settings = validate_settings(
            {"order": order_config, "activity": activity_config},
            template_type=category["template_type"],
            set_types=category.get("set_types"),
        )
    except ValueError:
        # 历史数据与品类档位不兼容时回落到品类默认参数，保证页面可打开并可重新保存
        settings = deepcopy(defaults)
    settings["activity"]["default_skc_rules"] = category_skc_rules(category)
    order_row = rows.get("order")
    activity_row = rows.get("activity")
    return {
        **_region_dict(region),
        "category": _category_brief(category),
        "order_strategy": order_row.strategy if order_row else "standard_order_v1",
        "activity_strategy": activity_row.strategy if activity_row else "standard_activity_v1",
        "order_version": order_row.version if order_row else 1,
        "activity_version": activity_row.version if activity_row else 1,
        "settings": settings,
    }


def list_regions(include_disabled: bool = False) -> list[dict]:
    with SessionLocal() as session:
        statement = select(Region)
        if not include_disabled:
            statement = statement.where(Region.enabled.is_(True))
        rows = session.scalars(statement.order_by(Region.sort_order, Region.id)).all()
        return [_region_dict(row) for row in rows]


def get_region_profile(code: str | None = None, category_code: str | None = None, include_disabled: bool = False) -> dict:
    normalized = (code or "").strip().upper()
    category = get_category(category_code, include_disabled=include_disabled)
    with SessionLocal() as session:
        statement = select(Region)
        if normalized:
            statement = statement.where(Region.code == normalized)
        else:
            statement = statement.where(Region.is_default.is_(True))
        if not include_disabled:
            statement = statement.where(Region.enabled.is_(True))
        region = session.scalar(statement.order_by(Region.sort_order, Region.id))
        if not region and not normalized:
            fallback = select(Region)
            if not include_disabled:
                fallback = fallback.where(Region.enabled.is_(True))
            region = session.scalar(fallback.order_by(Region.sort_order, Region.id))
        if not region:
            raise ValueError("区域不存在或已停用")
        return _profile(session, region, category)


def region_snapshot(code: str | None = None, category_code: str | None = None, include_disabled: bool = False) -> dict:
    profile = get_region_profile(code, category_code, include_disabled=include_disabled)
    return {
        "region": {key: profile[key] for key in ("id", "code", "name", "currency")},
        "category": profile["category"],
        "strategies": {"order": profile["order_strategy"], "activity": profile["activity_strategy"]},
        "versions": {"order": profile["order_version"], "activity": profile["activity_version"]},
        "settings": deepcopy(profile["settings"]),
    }


def create_region(payload: dict, updated_by: int | None = None) -> dict:
    code = str(payload.get("code", "")).strip().upper()
    name = str(payload.get("name", "")).strip()
    currency = str(payload.get("currency", "CNY")).strip().upper()
    copy_from = str(payload.get("copy_from", "")).strip().upper() or None
    if not REGION_CODE_RE.fullmatch(code):
        raise ValueError("区域代码需为2-16位大写字母、数字、下划线或短横线")
    if not name or len(name) > 80:
        raise ValueError("区域名称不能为空且不能超过80个字符")
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("币种代码必须是3位大写字母")
    with SessionLocal.begin() as session:
        if session.scalar(select(Region).where(Region.code == code)):
            raise ValueError("区域代码已存在")
        source_id = None
        if copy_from:
            source = session.scalar(select(Region).where(Region.code == copy_from))
            if not source:
                raise ValueError("复制的源区域不存在")
            source_id = source.id
        region = Region(code=code, name=name, currency=currency, enabled=True, is_default=False, sort_order=int(payload.get("sort_order", 100)))
        session.add(region)
        session.flush()
        # 为每个品类生成配置：优先复制源区域同品类配置，否则使用品类默认参数
        for category_row in session.scalars(select(Category).order_by(Category.sort_order, Category.id)).all():
            category = {
                "id": category_row.id,
                "code": category_row.code,
                "name": category_row.name,
                "template_type": category_row.template_type,
                "set_types": json.loads(category_row.set_types) if category_row.set_types else [],
            }
            defaults = category_defaults(category["template_type"], category["set_types"])
            for module, default_config in (("order", defaults["order"]), ("activity", defaults["activity"])):
                config = default_config
                if source_id is not None:
                    source_row = session.scalar(
                        select(RegionConfig).where(RegionConfig.region_id == source_id, RegionConfig.category_id == category_row.id, RegionConfig.module == module)
                    )
                    if source_row:
                        try:
                            value = json.loads(source_row.config_json)
                            if isinstance(value, dict):
                                config = value
                        except json.JSONDecodeError:
                            pass
                session.add(RegionConfig(
                    region_id=region.id,
                    category_id=category_row.id,
                    module=module,
                    strategy="standard_order_v1" if module == "order" else "standard_activity_v1",
                    config_json=json.dumps(config, ensure_ascii=False),
                    version=1,
                    updated_by=updated_by,
                ))
    return get_region_profile(code, include_disabled=True)


def update_region(code: str, payload: dict, updated_by: int | None = None, category_code: str | None = None) -> dict:
    normalized = code.strip().upper()
    category = get_category(category_code, include_disabled=True)
    settings = validate_settings(payload.get("settings", {}), template_type=category["template_type"], set_types=category.get("set_types"))
    order_strategy = str(payload.get("order_strategy", "standard_order_v1"))
    activity_strategy = str(payload.get("activity_strategy", "standard_activity_v1"))
    if order_strategy not in ORDER_STRATEGIES or activity_strategy not in ACTIVITY_STRATEGIES:
        raise ValueError("计算策略不受支持")
    name = str(payload.get("name", "")).strip()
    currency = str(payload.get("currency", "CNY")).strip().upper()
    if not name or len(name) > 80:
        raise ValueError("区域名称不能为空且不能超过80个字符")
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("币种代码必须是3位大写字母")
    enabled = bool(payload.get("enabled", True))
    make_default = bool(payload.get("is_default", False))
    if make_default and not enabled:
        raise ValueError("默认区域不能停用")
    with SessionLocal.begin() as session:
        region = session.scalar(select(Region).where(Region.code == normalized))
        if not region:
            raise ValueError("区域不存在")
        if region.is_default and not enabled:
            raise ValueError("默认区域不能停用")
        if make_default:
            for row in session.scalars(select(Region).where(Region.is_default.is_(True))).all():
                row.is_default = False
        region.name = name
        region.currency = currency
        region.enabled = enabled
        region.is_default = make_default or region.is_default
        region.sort_order = int(payload.get("sort_order", region.sort_order))
        region.updated_at = datetime.now(timezone.utc)
        rows = {row.module: row for row in session.scalars(select(RegionConfig).where(RegionConfig.region_id == region.id, RegionConfig.category_id == category["id"])).all()}
        for module, strategy in (("order", order_strategy), ("activity", activity_strategy)):
            row = rows.get(module)
            encoded = json.dumps(settings[module], ensure_ascii=False)
            if row:
                changed = row.strategy != strategy or row.config_json != encoded
                row.strategy = strategy
                row.config_json = encoded
                if changed:
                    row.version += 1
                row.updated_by = updated_by
                row.updated_at = datetime.now(timezone.utc)
            else:
                session.add(RegionConfig(region_id=region.id, category_id=category["id"], module=module, strategy=strategy, config_json=encoded, version=1, updated_by=updated_by))
    return get_region_profile(normalized, category_code=category["code"], include_disabled=True)


def delete_region(code: str) -> None:
    normalized = code.strip().upper()
    with SessionLocal.begin() as session:
        region = session.scalar(select(Region).where(Region.code == normalized))
        if not region:
            raise ValueError("区域不存在")
        if region.is_default:
            raise ValueError("默认区域不能删除")
        session.query(RegionConfig).filter(RegionConfig.region_id == region.id).delete()
        session.delete(region)
