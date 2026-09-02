from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import DEFAULT_SETTINGS, Region, RegionConfig, SessionLocal, get_activity_skc_rules
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


def _profile(session, region: Region) -> dict:
    rows = {row.module: row for row in session.scalars(select(RegionConfig).where(RegionConfig.region_id == region.id)).all()}
    settings = validate_settings({
        "order": _config_value(rows.get("order"), DEFAULT_SETTINGS["order"]),
        "activity": _config_value(rows.get("activity"), DEFAULT_SETTINGS["activity"]),
    })
    settings["activity"]["default_skc_rules"] = get_activity_skc_rules()
    order_row = rows.get("order")
    activity_row = rows.get("activity")
    return {
        **_region_dict(region),
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


def get_region_profile(code: str | None = None, include_disabled: bool = False) -> dict:
    normalized = (code or "").strip().upper()
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
        return _profile(session, region)


def region_snapshot(code: str | None = None) -> dict:
    profile = get_region_profile(code)
    return {
        "region": {key: profile[key] for key in ("id", "code", "name", "currency")},
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
    source = get_region_profile(copy_from, include_disabled=True) if copy_from else get_region_profile(None, include_disabled=True)
    with SessionLocal.begin() as session:
        if session.scalar(select(Region).where(Region.code == code)):
            raise ValueError("区域代码已存在")
        region = Region(code=code, name=name, currency=currency, enabled=True, is_default=False, sort_order=int(payload.get("sort_order", 100)))
        session.add(region)
        session.flush()
        session.add_all([
            RegionConfig(region_id=region.id, module="order", strategy=source["order_strategy"], config_json=json.dumps(source["settings"]["order"], ensure_ascii=False), version=1, updated_by=updated_by),
            RegionConfig(region_id=region.id, module="activity", strategy=source["activity_strategy"], config_json=json.dumps(source["settings"]["activity"], ensure_ascii=False), version=1, updated_by=updated_by),
        ])
    return get_region_profile(code, include_disabled=True)


def update_region(code: str, payload: dict, updated_by: int | None = None) -> dict:
    normalized = code.strip().upper()
    settings = validate_settings(payload.get("settings", {}))
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
        rows = {row.module: row for row in session.scalars(select(RegionConfig).where(RegionConfig.region_id == region.id)).all()}
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
                session.add(RegionConfig(region_id=region.id, module=module, strategy=strategy, config_json=encoded, version=1, updated_by=updated_by))
    return get_region_profile(normalized, include_disabled=True)


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