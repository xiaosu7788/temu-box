"""客服自助工具（只读部分）：工具清单、工具卡片、会话消息、查商品。

链路（与 Temu 后台「客服 → 联系官方客服 → 自助工具」一一对应）：
    1. querySelfServiceTools      4 个工具定义（toolId 由服务端下发，不硬编码）
    2. queryMessage               读会话，找 contentType=6 的工具卡片（含下架原因枚举）
    3. queryProduct*              查商品：SKC / SKU / SPU 各有一个接口
    4. sendMessage                提交 —— P3 才做，这里不碰

只读原则：本模块**不发起任何写操作**，提交能力留在 P3 的 action 事务里。
"""
from __future__ import annotations

import threading
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.services import temu_bridge
from app.services.store_goods import require_account
from app.services.store_session import StoreError, translate_bridge_error
from app.services.temu_bridge import BridgeError

TOOLS_CACHE_TTL = timedelta(minutes=30)
MAX_MESSAGES = 50
# 卡片「新鲜度」阈值：超过这个时间就重新发工具名文本唤醒
# （平台校验的是会话里的卡片上下文；老卡片 msgId 能翻到，但提交会被降级）
FRESH_CARD_MS = 10 * 60 * 1000
# 翻页找卡片的深度：实测最新 50 条没有时，往前 1 页就能找到；3 页足够，避免每次翻 10 页拖慢提交
CARD_MAX_PAGES = 3
# 唤醒后稍等再提交：wakeTool 内部已经等到卡片出现（卡片出现即上下文生效），
# 这里只留一点点缓冲，避免把每次唤醒都拖慢
WAKE_SETTLE_SECONDS = 0.5

# 卡片缓存：create_action / 提交前都要 ensure，不缓存会把每项都拖成一次翻页 + 唤醒
_CARD_CACHE: dict[str, dict] = {}
_CARD_CACHE_MAX = 200


def _cache_get(key: str, max_age_ms: int) -> Optional[dict]:
    item = _CARD_CACHE.get(key)
    if not item:
        return None
    if (time.monotonic() - item["at"]) * 1000 > max_age_ms:
        return None
    return item["card"]


def _cache_put(key: str, card_data: dict) -> None:
    if len(_CARD_CACHE) >= _CARD_CACHE_MAX:
        _CARD_CACHE.clear()
    _CARD_CACHE[key] = {"at": time.monotonic(), "card": card_data}


def invalidate_card_cache(account: Optional[str] = None) -> None:
    """登录/退出时清掉卡片缓存，避免复用上一个会话的 msgId。"""
    if not account:
        _CARD_CACHE.clear()
        return
    prefix = f"{account}|"
    for key in [k for k in _CARD_CACHE if k.startswith(prefix)]:
        _CARD_CACHE.pop(key, None)

# contentType 枚举（服务端下发，前端只做展示映射）
CONTENT_TYPE_NAMES = {
    1: "文本",
    2: "图片",
    3: "视频",
    4: "卡片",
    5: "问题",
    6: "自助工具卡",
    7: "工具提交",
    8: "用户反馈",
    9: "转人工",
    10: "快捷工具",
    12: "机器人回执",
    100: "时间标签",
    101: "机器人FAQ",
    102: "机器人猜测",
    103: "机器人回执",
}

DATA_TYPE_NAMES = {1: "SKC", 2: "SKU", 3: "采购单", 4: "货品SKC", 5: "SPU", 6: "库存"}
SUPPORTED_QUERY_TYPES = {"SKC", "SKU", "SPU"}

_tools_cache: dict[str, tuple[datetime, list[dict]]] = {}
_cache_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value) -> str:
    return "" if value is None else str(value)


def _int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_SPLIT_RE = re.compile(r"[\s,，;；、|]+")
MAX_BATCH_IDS = 50


def normalize_ids(raw) -> list[str]:
    """把多行/逗号/空格混排的输入解析成去重后的纯数字编号（保序，最多 50 个）。"""
    if isinstance(raw, (list, tuple)):
        chunks = [str(item) for item in raw]
    else:
        chunks = _SPLIT_RE.split(str(raw or ""))
    seen: set[str] = set()
    result: list[str] = []
    for chunk in chunks:
        value = chunk.strip()
        if not value or not value.isdigit() or value in seen:
            continue
        seen.add(value)
        result.append(value)
        if len(result) >= MAX_BATCH_IDS:
            break
    return result


# ==================== 工具定义 ====================
def _option(option: dict) -> dict:
    code = _int(option.get("code"))
    desc = option.get("desc") or option.get("title") or ""
    return {
        "code": code,
        "desc": desc,
        # 2008（其他）需要用户补具体原因
        "need_input": bool(option.get("needInput")) or code == 2008,
    }


def _tool(tool: dict) -> dict:
    options = tool.get("optionItemList") or []
    data_id_type = _int(tool.get("dataIdType"))
    return {
        "name": tool.get("toolName") or "",
        "tool_id": _text(tool.get("toolId")),
        "data_id_type": data_id_type,
        "data_type": DATA_TYPE_NAMES.get(data_id_type, _text(data_id_type)),
        "enabled": True if tool.get("ableUse") is None else bool(tool.get("ableUse")),
        "only_fully_hosted": bool(tool.get("onlyFullyHosted")),
        "option_title": tool.get("optionTitle"),
        "options": [_option(item) for item in options],
        "rate_limit_ms": _int(tool.get("rateLimitMs")),
        "success_tip": tool.get("successTip"),
        "fail_tip": tool.get("failTip"),
    }


def tools(user_id: int, *, force: bool = False) -> list[dict]:
    """4 个自助工具的定义。toolId 由服务端下发，缓存 30 分钟。"""
    account = require_account(user_id)
    now = _now()
    with _cache_lock:
        cached = _tools_cache.get(account)
        if not force and cached and now - cached[0] < TOOLS_CACHE_TTL:
            return cached[1]

    try:
        data = temu_bridge.kefu_tools(account)
    except BridgeError as exc:
        raise translate_bridge_error(exc) from None

    items = [_tool(tool) for tool in data.get("tools") or []]
    # ⚠️ 空列表不能缓存：平台刚 connect 完偶尔返回空，缓存 30 分钟会让页面一直"没有工具定义"
    if items:
        with _cache_lock:
            _tools_cache[account] = (now, items)
    return items


def invalidate_tools_cache(account: Optional[str] = None) -> None:
    with _cache_lock:
        if account:
            _tools_cache.pop(account, None)
        else:
            _tools_cache.clear()


def find_tool(user_id: int, name_or_id: str) -> Optional[dict]:
    """按名称/ID 找工具（P3 提交时用；名称支持包含匹配）。"""
    needle = (name_or_id or "").strip()
    if not needle:
        return None
    for tool in tools(user_id):
        if needle == tool["tool_id"] or needle == tool["name"] or needle in tool["name"]:
            return tool
    return None


# ==================== 工具卡片 ====================
def card(user_id: int, *, tool_id: Optional[str] = None, max_pages: int = 10) -> dict:
    """最新一张自助工具卡片（contentType=6），带下架原因枚举。

    卡片只在会话早期推送，之后会被聊天记录挤出「最近 50 条」窗口，
    所以 bridge 侧会按 tool_id 精确找并自动向前翻页。
    """
    account = require_account(user_id)
    try:
        data = temu_bridge.kefu_card(account, tool_id=tool_id, max_pages=max_pages)
    except BridgeError as exc:
        error = translate_bridge_error(exc)
        if error.code == "EMPTY":
            raise StoreError(
                "未找到自助工具卡片：若已建立过客服会话，说明卡片已被聊天记录挤出可见范围，"
                "请在 Temu 后台「联系官方客服 → 自助工具」里重新点一次目标工具，让机器人推送新卡片后重试",
                code="CARD_EMPTY",
                status=404,
            ) from None
        raise error from None

    data_type = _int(data.get("dataType"))
    options = data.get("optionItemList") or []
    return {
        "msg_id": _text(data.get("msgId")),
        "tool_id": _text(data.get("toolId")),
        "data_id_type": data_type,
        "data_type": DATA_TYPE_NAMES.get(data_type, _text(data_type)),
        "data_type_names": [DATA_TYPE_NAMES.get(_int(item), _text(item)) for item in data.get("dataTypeList") or []],
        "option_title": data.get("optionTitle"),
        "options": [_option(item) for item in options],
        "popup": data.get("popUpItem"),
        "page": data.get("page"),
        "card": data.get("card"),
    }


def cards(user_id: int, *, max_pages: int = 10) -> list[dict]:
    """每个工具的最新卡片（按 toolId 去重，可用于预热 parentMsgId）。"""
    account = require_account(user_id)
    try:
        data = temu_bridge.kefu_cards(account, max_pages=max_pages)
    except BridgeError as exc:
        raise translate_bridge_error(exc) from None
    return [
        {
            "tool_id": _text(item.get("toolId")),
            "msg_id": _text(item.get("msgId")),
            "data_id_type": _int(item.get("dataType")),
            "option_title": item.get("optionTitle"),
            "options": [_option(option) for option in item.get("optionItemList") or []],
        }
        for item in data.get("cards") or []
    ]


def ensure_card(
    user_id: int,
    *,
    tool_name: str = "",
    tool_id: Optional[str] = None,
    max_age_ms: int = FRESH_CARD_MS,
    timeout_ms: int = 30000,
) -> dict:
    """确保有「新鲜」的工具卡片：够新就复用，太旧或找不到就发工具名文本唤醒。

    为什么必须有新鲜度判断：平台校验的是「会话里有没有有效的卡片上下文」。
    翻页能找到 40 天前的老卡片，但拿它直接提交会被降级
    （回执「已进入加速等待序列」，几分钟后必然「系统忙碌」失败）。
    """
    account = require_account(user_id)
    if not tool_name:
        return card(user_id, tool_id=tool_id)

    cache_key = f"{account}|{tool_id or '*'}"
    cached = _cache_get(cache_key, max_age_ms)
    if cached:
        return cached

    try:
        data = temu_bridge.kefu_ensure(
            account,
            tool_name,
            tool_id=tool_id,
            max_age_ms=max_age_ms,
            max_pages=CARD_MAX_PAGES,
            timeout_ms=timeout_ms,
        )
    except BridgeError as exc:
        raise translate_bridge_error(exc) from None

    result = {
        "msg_id": _text(data.get("msgId")),
        "tool_id": _text(data.get("toolId")),
        "data_id_type": None,
        "data_type": "",
        "data_type_names": [],
        "option_title": data.get("optionTitle"),
        "options": [_option(item) for item in data.get("optionItemList") or []],
        "popup": None,
        "page": None,
        "card": None,
        "waited_ms": data.get("waitedMs"),
        # find = 复用了新鲜卡片；wake = 刚发文本唤醒
        "source": data.get("source") or "find",
    }
    if result["msg_id"]:
        _cache_put(cache_key, result)
    # 刚唤醒时等一拍再提交：平台把新卡片计入上下文需要一点时间
    if result["source"] == "wake":
        time.sleep(WAKE_SETTLE_SECONDS)
    return result


# ==================== 会话消息 ====================
def messages(user_id: int, *, limit: int = MAX_MESSAGES) -> dict:
    account = require_account(user_id)
    safe_limit = max(1, min(int(limit or MAX_MESSAGES), MAX_MESSAGES))
    try:
        data = temu_bridge.kefu_messages(account, limit=safe_limit)
    except BridgeError as exc:
        raise translate_bridge_error(exc) from None

    items = []
    for message in data.get("items") or []:
        content_type = _int(message.get("contentType"))
        items.append({
            "msg_id": _text(message.get("msgId")),
            "content_type": content_type,
            "content_type_name": CONTENT_TYPE_NAMES.get(content_type, _text(content_type)),
            "timestamp": message.get("timestamp"),
            "content": message.get("content") or "",
        })
    return {"total": int(data.get("total") or len(items)), "items": items, "limit": safe_limit}


# ==================== 查商品 ====================
def query_product(user_id: int, data_type: str, data_id: str, tool_id: Optional[str] = None) -> dict:
    """只读查询：用于提交前确认商品身份（P3 的确认弹窗复用它）。"""
    account = require_account(user_id)
    normalized = (data_type or "").strip().upper()
    if normalized not in SUPPORTED_QUERY_TYPES:
        raise StoreError("查询类型必须是 SKC / SKU / SPU", code="ARGS", status=400)
    identifier = _text(data_id).strip()
    if not identifier:
        raise StoreError("请输入商品 ID", code="ARGS", status=400)
    if not identifier.isdigit():
        raise StoreError("商品 ID 必须是纯数字", code="ARGS", status=400)

    try:
        data = temu_bridge.kefu_query(account, normalized, identifier, tool_id or None)
    except BridgeError as exc:
        raise translate_bridge_error(exc) from None

    return {
        "ok": bool(data.get("ok")),
        "code": _text(data.get("code")),
        "message": data.get("msg") or "",
        "found": bool(data.get("productName")),
        "product_name": data.get("productName") or "",
        "product_picture": data.get("productPicture") or "",
        "product_skc_id": _text(data.get("productSkcId")),
        "product_id": _text(data.get("productId")),
        "algo_product_id": _text(data.get("algoProductId")),
        "is_semi": data.get("isSemi"),
        "is_cloth": data.get("isCloth"),
        "has_bonded_or_abroad_stock": data.get("hasBondedOrAbroadStock"),
    }


def query_batch(user_id: int, data_type: str, ids, tool_id: Optional[str] = None) -> dict:
    """批量只读查询：把一串编号换成商品详情（串行，避免撞限流）。

    只读、可反复执行；单个编号失败不会中断整批，失败原因随行返回。
    """
    account = require_account(user_id)
    normalized = (data_type or "").strip().upper()
    if normalized not in SUPPORTED_QUERY_TYPES:
        raise StoreError("查询类型必须是 SKC / SKU / SPU", code="ARGS", status=400)
    parsed = normalize_ids(ids)
    if not parsed:
        raise StoreError("请粘贴至少一个纯数字编号", code="ARGS", status=400)

    items = []
    for data_id in parsed:
        try:
            data = temu_bridge.kefu_query(account, normalized, data_id, tool_id or None)
            items.append({
                "data_id": data_id,
                "ok": bool(data.get("ok")),
                "found": bool(data.get("productName")),
                "product_name": data.get("productName") or "",
                "product_picture": data.get("productPicture") or "",
                "product_skc_id": _text(data.get("productSkcId")),
                "product_id": _text(data.get("productId")),
                "message": data.get("msg") or "",
                "error_code": "",
            })
        except BridgeError as exc:
            items.append({
                "data_id": data_id,
                "ok": False,
                "found": False,
                "product_name": "",
                "product_picture": "",
                "product_skc_id": "",
                "product_id": "",
                "message": exc.message,
                "error_code": str(exc.code or ""),
            })
    return {
        "data_type": normalized,
        "total": len(items),
        "items": items,
        "truncated": len(parsed) >= MAX_BATCH_IDS,
    }
