"""品类（模版类型）管理：参数形态由「品类 × 模版类型」决定。

三种模版统一抽象为「套装档位列表」：
- set_based   套装型：单品 + 4/5/6/8/10/12 件套（现状）
- no_set      无套装型：仅单品，头程统一单价，活动价只算单品底价
- custom_set  自定义套装型：管理员自选套装档位
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from app.database import Category, Region, RegionConfig, SessionLocal, get_activity_skc_rules
from app.services.activity import normalize_parse_config

CATEGORY_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_-]{0,15}$")

TEMPLATE_TYPES = {
    "set_based": {
        "label": "套装型",
        "description": "单品 + 4/5/6/8/10/12 件套完整档位，适合有套装系列的品类",
        "customizable": False,
    },
    "no_set": {
        "label": "无套装型",
        "description": "仅单品：头程统一单价，活动价只按单品底价计算",
        "customizable": False,
    },
    "custom_set": {
        "label": "自定义套装型",
        "description": "自选套装档位（如仅 4/6/8 件套），其余逻辑与套装型一致",
        "customizable": True,
    },
}

BASE_ORDER_TIERS = ["单品", "4件套", "5件套", "6件套", "8件套", "10件套", "12件套"]
BASE_SET_KEYS = [4, 5, 6, 8, 10, 12]
BASE_SET_PRICES = {4: 42.0, 5: 45.0, 6: 48.0, 8: 71.0, 10: 75.0, 12: 92.0}
BASE_ORDER_HEADCOST = {"单品": 5.0, "4件套": 5.0, "5件套": 5.0, "6件套": 5.0, "8件套": 10.0, "10件套": 10.0, "12件套": 15.0}
MIN_SET_PIECES = 2
MAX_SET_PIECES = 30
MAX_CUSTOM_TIERS = 12


def normalize_set_types(value) -> list[int]:
    """custom_set 品类的套装档位（件数），去重升序。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError("自定义套装型必须配置至少一个套装档位")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("套装档位格式不正确") from exc
    if not isinstance(value, list) or not value:
        raise ValueError("自定义套装型必须配置至少一个套装档位")
    if len(value) > MAX_CUSTOM_TIERS:
        raise ValueError(f"自定义套装档位最多 {MAX_CUSTOM_TIERS} 个")
    keys: list[int] = []
    for item in value:
        try:
            pieces = int(item)
        except (TypeError, ValueError) as exc:
            raise ValueError("套装档位必须是整数（件数）") from exc
        if not MIN_SET_PIECES <= pieces <= MAX_SET_PIECES:
            raise ValueError(f"套装档位件数必须在 {MIN_SET_PIECES}-{MAX_SET_PIECES} 之间")
        if pieces not in keys:
            keys.append(pieces)
    return sorted(keys)


def resolve_tiers(template_type: str, set_types=None) -> tuple[list[str], list[int]]:
    """返回 (订单头程档位, 活动套装件数档位)。所有校验/表单/计算均由此派生。"""
    if template_type == "no_set":
        return ["单品"], []
    if template_type == "custom_set":
        keys = normalize_set_types(set_types)
        return ["单品"] + [f"{key}件套" for key in keys], keys
    if template_type == "set_based":
        return list(BASE_ORDER_TIERS), list(BASE_SET_KEYS)
    raise ValueError("不支持的模版类型")


def _decode_set_types(row: Category) -> list[int]:
    if row.template_type != "custom_set" or not row.set_types:
        return []
    try:
        return normalize_set_types(json.loads(row.set_types))
    except (ValueError, json.JSONDecodeError, TypeError):
        return []


def _decode_allowed_regions(row: Category) -> Optional[list[str]]:
    """None = 全部区域开放；否则为开放的区域代码列表。"""
    if not row.allowed_regions:
        return None
    try:
        value = json.loads(row.allowed_regions)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, list) and value else None


def _normalize_allowed_regions(value) -> Optional[list[str]]:
    """校验并规范化开放区域：None/空 = 全部区域；数组须为已存在区域的代码。"""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("开放区域格式不正确") from exc
    if not isinstance(value, list):
        raise ValueError("开放区域格式不正确")
    codes = [str(item).strip().upper() for item in value if str(item).strip()]
    if not codes:
        return None
    unique = sorted(set(codes))
    with SessionLocal() as session:
        unknown = set(unique) - set(session.scalars(select(Region.code)).all())
    if unknown:
        raise ValueError(f"区域不存在：{', '.join(sorted(unknown))}")
    return unique


def _category_dict(row: Category) -> dict:
    template = TEMPLATE_TYPES.get(row.template_type, TEMPLATE_TYPES["set_based"])
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "template_type": row.template_type,
        "template_label": template["label"],
        "set_types": _decode_set_types(row),
        "allowed_regions": _decode_allowed_regions(row),
        "enabled": row.enabled,
        "is_default": row.is_default,
        "sort_order": row.sort_order,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def category_defaults(template_type: str, set_types=None) -> dict:
    """按模版生成一套默认 order/activity 参数（新建品类/区域时种子）。"""
    order_tiers, set_keys = resolve_tiers(template_type, set_types)
    headcost = {tier: BASE_ORDER_HEADCOST.get(tier, 5.0) for tier in order_tiers}
    set_prices = {str(key): BASE_SET_PRICES.get(key, 42.0) for key in set_keys}
    skc_rules = {
        "set_keywords": ["piece", "件套", "套装"] if set_keys else [],
        "set_mappings": [],
        "single_mode": "last_segment",
        "single_delimiter": "-",
        "single_marker": "price",
    }
    return {
        "order": {
            "headcost": headcost,
            "operation_fee": 7,
            "extra_item_fee": 2,
            "tail_fee": 0,
            "shipping_subsidy": 0,
        },
        "activity": {
            "headcost": 5,
            "operation_fee": 7,
            "uplift_limit": 1,
            "set_prices": set_prices,
            "single_tiers": [{"min_price": 0, "profit": 0}],
            "id_profit_rules": [],
            "default_skc_rules": skc_rules,
        },
    }


def list_categories(include_disabled: bool = False, region_code: Optional[str] = None) -> list[dict]:
    """品类列表；region_code 提供时只返回对该区域开放的品类（NULL = 全部开放）。"""
    normalized_region = (region_code or "").strip().upper()
    with SessionLocal() as session:
        statement = select(Category)
        if not include_disabled:
            statement = statement.where(Category.enabled.is_(True))
        rows = session.scalars(statement.order_by(Category.sort_order, Category.id)).all()
        items = [_category_dict(row) for row in rows]
    if normalized_region:
        items = [item for item in items if item["allowed_regions"] is None or normalized_region in item["allowed_regions"]]
    return items


def default_category_id() -> int:
    with SessionLocal() as session:
        row = session.scalar(select(Category).where(Category.is_default.is_(True)).order_by(Category.sort_order, Category.id))
        if not row:
            row = session.scalar(select(Category).order_by(Category.sort_order, Category.id))
        if not row:
            raise ValueError("系统中没有可用品类")
        return row.id


def get_category(code: Optional[str] = None, include_disabled: bool = False) -> dict:
    normalized = (code or "").strip().upper()
    with SessionLocal() as session:
        statement = select(Category)
        if normalized:
            statement = statement.where(Category.code == normalized)
        else:
            statement = statement.where(Category.is_default.is_(True))
        if not include_disabled:
            statement = statement.where(Category.enabled.is_(True))
        row = session.scalar(statement.order_by(Category.sort_order, Category.id))
        if not row and not normalized:
            fallback = select(Category)
            if not include_disabled:
                fallback = fallback.where(Category.enabled.is_(True))
            row = session.scalar(fallback.order_by(Category.sort_order, Category.id))
        if not row:
            raise ValueError("品类不存在或已停用")
        return _category_dict(row)


def get_category_by_id(category_id: Optional[int]) -> dict:
    if category_id is None:
        return get_category()
    with SessionLocal() as session:
        row = session.get(Category, category_id)
        if not row:
            raise ValueError("品类不存在")
        return _category_dict(row)


def category_allowed_pieces(category: dict) -> frozenset[int]:
    """品类允许的套装件数档位（无套装型为空集）。"""
    _, set_keys = resolve_tiers(category["template_type"], category.get("set_types"))
    return frozenset(set_keys)


def category_skc_rules(category: dict) -> dict:
    """读取品类的默认 SKC 识别规则（容错：异常数据回落到品类默认规则）。"""
    allowed = category_allowed_pieces(category)
    try:
        rules = normalize_parse_config(get_activity_skc_rules(category["code"]), allowed_pieces=allowed)
    except (ValueError, TypeError):
        rules = None
    if rules is None:
        rules = normalize_parse_config(category_defaults(category["template_type"], category.get("set_types"))["activity"]["default_skc_rules"], allowed_pieces=allowed)
    return rules


def _seed_region_configs(session, category: dict, config: dict) -> None:
    """为所有区域生成该品类的 order/activity 配置。"""
    for region in session.scalars(select(Region)).all():
        session.add_all([
            RegionConfig(region_id=region.id, category_id=category["id"], module="order", strategy="standard_order_v1", config_json=json.dumps(config["order"], ensure_ascii=False), version=1),
            RegionConfig(region_id=region.id, category_id=category["id"], module="activity", strategy="standard_activity_v1", config_json=json.dumps(config["activity"], ensure_ascii=False), version=1),
        ])


def create_category(payload: dict, updated_by: Optional[int] = None) -> dict:
    code = str(payload.get("code", "")).strip().upper()
    name = str(payload.get("name", "")).strip()
    template_type = str(payload.get("template_type", "set_based")).strip()
    if not CATEGORY_CODE_RE.fullmatch(code):
        raise ValueError("品类代码需为1-16位大写字母、数字、下划线或短横线")
    if not name or len(name) > 80:
        raise ValueError("品类名称不能为空且不能超过80个字符")
    if template_type not in TEMPLATE_TYPES:
        raise ValueError("不支持的模版类型")
    set_types = normalize_set_types(payload.get("set_types")) if template_type == "custom_set" else []
    allowed_regions = _normalize_allowed_regions(payload.get("allowed_regions"))
    with SessionLocal.begin() as session:
        if session.scalar(select(Category).where(Category.code == code)):
            raise ValueError("品类代码已存在")
        category = Category(code=code, name=name, template_type=template_type, set_types=json.dumps(set_types) if set_types else None, allowed_regions=json.dumps(allowed_regions) if allowed_regions else None, enabled=True, is_default=False, sort_order=int(payload.get("sort_order", 100)))
        session.add(category)
        session.flush()
        result = _category_dict(category)
        _seed_region_configs(session, result, category_defaults(template_type, set_types))
    return result


def update_category(code: str, payload: dict, updated_by: Optional[int] = None) -> dict:
    normalized = code.strip().upper()
    name = str(payload.get("name", "")).strip()
    template_type = str(payload.get("template_type", "set_based")).strip()
    if not name or len(name) > 80:
        raise ValueError("品类名称不能为空且不能超过80个字符")
    if template_type not in TEMPLATE_TYPES:
        raise ValueError("不支持的模版类型")
    set_types = normalize_set_types(payload.get("set_types")) if template_type == "custom_set" else []
    allowed_regions = _normalize_allowed_regions(payload.get("allowed_regions"))
    enabled = bool(payload.get("enabled", True))
    make_default = bool(payload.get("is_default", False))
    if make_default and not enabled:
        raise ValueError("默认品类不能停用")
    with SessionLocal.begin() as session:
        category = session.scalar(select(Category).where(Category.code == normalized))
        if not category:
            raise ValueError("品类不存在")
        if category.template_type != template_type:
            raise ValueError("模版类型创建后不可变更（档位结构不同，请删除后新建品类）")
        if category.is_default and not enabled:
            raise ValueError("默认品类不能停用")
        if make_default:
            for row in session.scalars(select(Category).where(Category.is_default.is_(True))).all():
                row.is_default = False
        category.name = name
        category.template_type = template_type
        category.set_types = json.dumps(set_types) if set_types else None
        category.allowed_regions = json.dumps(allowed_regions) if allowed_regions else None
        category.enabled = enabled
        category.is_default = make_default or category.is_default
        category.sort_order = int(payload.get("sort_order", category.sort_order))
        category.updated_at = datetime.now(timezone.utc)
        result = _category_dict(category)
        # 品类首次创建时已种子配置；兼容历史数据缺失配置行的情况
        if not session.scalars(select(RegionConfig.id).where(RegionConfig.category_id == category.id).limit(1)).first():
            _seed_region_configs(session, result, category_defaults(template_type, set_types))
    return result


def delete_category(code: str) -> None:
    normalized = code.strip().upper()
    with SessionLocal.begin() as session:
        category = session.scalar(select(Category).where(Category.code == normalized))
        if not category:
            raise ValueError("品类不存在")
        if category.is_default:
            raise ValueError("默认品类不能删除")
        from app.database import HalfHeadcostSku
        session.query(RegionConfig).filter(RegionConfig.category_id == category.id).delete()
        session.query(HalfHeadcostSku).filter(HalfHeadcostSku.category_id == category.id).delete()
        session.delete(category)
