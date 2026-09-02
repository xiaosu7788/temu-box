from __future__ import annotations

from copy import deepcopy

from app.database import DEFAULT_SETTINGS, get_settings, save_settings


def settings_public() -> dict:
    return get_settings()


def validate_settings(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("配置格式不正确")
    result = deepcopy(DEFAULT_SETTINGS)
    order = payload.get("order", {})
    activity = payload.get("activity", {})
    if not isinstance(order, dict) or not isinstance(activity, dict):
        raise ValueError("订单或活动配置格式不正确")

    headcost = order.get("headcost", result["order"]["headcost"])
    if not isinstance(headcost, dict):
        raise ValueError("订单头程配置格式不正确")
    for key in result["order"]["headcost"]:
        value = float(headcost.get(key, result["order"]["headcost"][key]))
        if value < 0 or value > 1000:
            raise ValueError("订单头程参数必须在 0-1000 之间")
        result["order"]["headcost"][key] = round(value, 2)
    for key in ("operation_fee", "extra_item_fee", "tail_fee", "shipping_subsidy"):
        value = float(order.get(key, result["order"][key]))
        if value < 0 or value > 1000:
            raise ValueError("订单费用参数必须在 0-1000 之间")
        result["order"][key] = round(value, 2)

    for key in ("headcost", "operation_fee"):
        value = float(activity.get(key, result["activity"][key]))
        if value < 0 or value > 1000:
            raise ValueError("活动费用参数必须在 0-1000 之间")
        result["activity"][key] = round(value, 2)
    set_prices = activity.get("set_prices", result["activity"]["set_prices"])
    if not isinstance(set_prices, dict):
        raise ValueError("多件套活动价配置格式不正确")
    result["activity"]["set_prices"] = {}
    for key in ("4", "5", "6", "8", "10", "12"):
        value = float(set_prices.get(key, result["activity"]["set_prices"].get(key, DEFAULT_SETTINGS["activity"]["set_prices"][key])))
        if value < 0 or value > 10000:
            raise ValueError("多件套活动价必须在 0-10000 之间")
        result["activity"]["set_prices"][key] = round(value, 2)
    tiers = activity.get("single_tiers", result["activity"]["single_tiers"])
    if not isinstance(tiers, list) or not tiers:
        raise ValueError("至少需要一个单品条件")
    normalized_tiers = []
    for tier in tiers:
        if not isinstance(tier, dict):
            raise ValueError("单品条件格式不正确")
        minimum = float(tier.get("min_price", 0))
        profit = float(tier.get("profit", 0))
        if minimum < 0 or profit < 0 or minimum > 100000 or profit > 100000:
            raise ValueError("单品货值和利润参数不合法")
        normalized_tiers.append({"min_price": round(minimum, 2), "profit": round(profit, 2)})
    result["activity"]["single_tiers"] = sorted(normalized_tiers, key=lambda item: item["min_price"])
    return result


def update_settings(payload: dict) -> dict:
    return save_settings(validate_settings(payload))
