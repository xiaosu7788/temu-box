"""客服写操作（下架 / 发布到站点 / 价格刷新 / 异常查询）的事务化封装。

设计要点（全部来自实测约束）：
1. **拦截检查有状态**：`preIntercept` 同一商品只有第一次返回 `interceptCode=0`，
   重复调用会得到「该商品上次咨询结果正在返回中，请勿重复咨询」。
   → 创建 action 时一次性完成 queryProduct + preIntercept + 组装 content，全部落库；
     确认阶段只发送已存的 request_body，**绝不重新拦截**。
2. **幂等**：一个 action 只能 send 一次；重复确认返回原结果，不再发消息。
3. **失败也留痕**：查询/拦截/卡片失败都会落一条 `stage=failed` 记录，
   批量执行时列表里每个编号都有对应行。
4. **危险动作**：默认只预览；确认必须显式调用 confirm；全局有系统级熔断开关
   （`system.kefu_writes_enabled`），关闭后所有人只能预览。
5. **回执**：`sendMessage` 后机器人约 3 秒推送 `contentType=12`，由前端轮询 reply 接口拉取。
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.database import (
    create_kefu_action,
    get_kefu_action,
    get_temu_session,
    latest_kefu_parent_msg_id,
    latest_kefu_sent_at,
    list_kefu_actions,
    update_kefu_action,
)
from app.services import store_kefu, system, temu_bridge
from app.services.store_goods import require_account
from app.services.store_session import StoreError, mask_account, translate_bridge_error
from app.services.temu_bridge import BridgeError

# 「其他」选项：需要用户补具体原因
REASON_NEED_INPUT = 2008
MAX_REASON_DETAIL = 200
CONTENT_TYPE_TOOL_SUBMIT = 7
# contentType=12 是平台给出的终态回执（含具体结果）；contentType=1 里这类话术只是「已受理」
TERMINAL_CONTENT_TYPE = 12

# 受理话术（平台已收下、正在处理）
# ⚠️ 这里**不能**放「加速等待」：那是降级信号，不是受理（见 DEGRADED_REPLY_HINTS）。
# ⚠️ 也不要放「稍后」：它太宽，会把「系统忙碌中，请您稍后重试」吞成 accepted。
PENDING_REPLY_HINTS = (
    "请稍候",
    "正在处理",
    "排队中",
)

# 降级信号：提交没有被平台的同步执行器接住，几秒内先回这句，
# 随后必然变成「系统忙碌中，请您稍后重试」。需要唤醒卡片上下文后重试。
DEGRADED_REPLY_HINTS = (
    "加速等待",
    "进入加速等待序列",
)

# 执行失败话术（平台侧临时错误，可重试）
# ⚠️ 必须先于 PENDING 判断：「系统忙碌中，请您稍后重试」里含「稍后」。
FAILED_REPLY_HINTS = (
    "系统忙碌",
    "操作失败",
    "稍后重试",
    "提交失败",
    "服务异常",
)

# 业务拒绝话术（不可重试，需人工介入）
REJECTED_REPLY_HINTS = (
    "还未发布到任何站点",
    "无法操作",
    "无权限",
    "不存在",
    "已被删除",
    "不符合",
)

# 失败自动重试（「系统忙碌」是平台限流，第一次别等太久）
MAX_RETRY = 3
RETRY_BACKOFF = (120, 420, 900)  # 2 分钟 / 7 分钟 / 15 分钟
# 降级重试要快：唤醒卡片后几秒内就能重发，不需要等 5 分钟
DEGRADED_RETRY_DELAY = 60
# 提交前要求卡片多新（超过就重新唤醒）：平台对卡片上下文有时效
SUBMIT_CARD_MAX_AGE_MS = 3 * 60 * 1000
# 同账号两次提交最小间隔（秒）。
# 上游实测 15 秒稳定；这里按运营要求取 5 秒（更激进，可能更容易撞「系统忙碌」，
# 但有降级/失败自动重试兜底，且批量每项失败只影响该行）。
MIN_SUBMIT_INTERVAL = 5

logger = logging.getLogger("temubox.store.kefu")


def classify_reply(message: dict) -> str:
    """区分「受理话术」「终态结果」「降级信号」「执行失败」「业务拒绝」。

    判定顺序不可调换：
      1. contentType=12 → 成功终态
      2. 失败话术（系统忙碌等）→ failed，可重试
      3. 降级话术（加速等待）→ degraded，需唤醒卡片后重试
      4. 受理话术 → accepted，继续等待
      5. 业务拒绝话术 → rejected，不可重试
      6. 其它 → unknown（不要默认成功）

    关键认知（2026-11 上游验证）：「已进入加速等待序列」**不是**受理回执，
    而是降级信号 —— 说明提交没被同步执行器接住，几分钟后必然「系统忙碌」。
    根因是会话里缺少新鲜的工具卡片上下文。
    """
    content_type = message.get("contentType")
    content = str(message.get("content") or "")

    if content_type == TERMINAL_CONTENT_TYPE:
        return "replied"

    if content_type == 1:
        if any(hint in content for hint in FAILED_REPLY_HINTS):
            return "failed"
        if any(hint in content for hint in DEGRADED_REPLY_HINTS):
            return "degraded"
        if any(hint in content for hint in PENDING_REPLY_HINTS):
            return "accepted"
        if any(hint in content for hint in REJECTED_REPLY_HINTS):
            return "rejected"

    # 宁可状态不明，也不误报成功
    return "unknown"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value) -> str:
    return "" if value is None else str(value)


def writes_enabled() -> bool:
    """全局熔断开关：默认开启，管理员可在系统设置里一键切成只读。"""
    try:
        return bool(system.get().get("kefu_writes_enabled", True))
    except Exception:  # noqa: BLE001 - 设置读取失败时不阻塞只读功能
        return True


def ensure_writes_enabled() -> None:
    if not writes_enabled():
        raise StoreError(
            "客服写操作已被管理员暂时关闭（只读模式），请联系管理员",
            code="WRITES_DISABLED",
            status=403,
        )


def _finalize(action: dict) -> dict:
    """对外视图：只做数据库字段透传。

    刻意不在这里查工具清单（那会 require_account 并要求会话有效），
    列表/详情接口必须能在会话失效时依然可读。
    """
    return action


def _build_content(tool: dict, product: dict, data_type: int, data_id: str, reason_code: Optional[int], reason_detail: str) -> dict:
    options = tool.get("options") or []
    chosen = next((option for option in options if option["code"] == reason_code), None)
    content = {
        "name": product.get("product_name") or "",
        "img": product.get("product_picture") or "",
        "dataId": str(data_id),
        "dataType": data_type,
        "toolId": int(tool["tool_id"]),
        "count": None,
        "optionTitle": tool.get("option_title"),
        "optionInfo": None,
    }
    if chosen:
        content["optionInfo"] = {
            "code": chosen["code"],
            "desc": chosen["desc"],
            # 「其他」才需要用户补具体原因
            "detailedReason": (reason_detail or None) if chosen["code"] == REASON_NEED_INPUT else None,
        }
    return content


def _record_failure(
    user_id: int,
    tool: dict,
    data_id: str,
    reason_code: Optional[int],
    reason_detail: str,
    message: str,
    *,
    batch_id: Optional[str] = None,
    account_masked: str = "",
) -> None:
    """失败也落一条 failed 记录：批量列表需要每行都能看到结果。"""
    try:
        create_kefu_action(
            uuid.uuid4().hex,
            user_id=user_id,
            batch_id=batch_id,
            account_masked=account_masked,
            tool_id=tool["tool_id"],
            tool_name=tool["name"],
            data_type=tool["data_id_type"] or 1,
            data_type_name=tool["data_type"],
            data_id=data_id,
            reason_code=reason_code,
            reason_detail=reason_detail,
            stage="failed",
            error=_text(message)[:255],
        )
    except Exception:  # noqa: BLE001 - 记录失败不应掩盖真实错误
        pass


def _resolve_tool(user_id: int, account: str, tool_id: str, tool_name: str, data_type: str) -> tuple[dict, str]:
    identifier = (tool_id or tool_name or "").strip()
    if not identifier:
        raise StoreError("请选择要使用的自助工具", code="ARGS", status=400)
    tool = store_kefu.find_tool(user_id, identifier)
    if not tool:
        # 缓存可能过期（服务端会换 toolId），强制刷新一次再找
        store_kefu.invalidate_tools_cache(account)
        tool = store_kefu.find_tool(user_id, identifier)
    if not tool:
        raise StoreError("未找到该自助工具，请刷新工具清单", code="TOOL_NOT_FOUND", status=404)
    if not tool["enabled"]:
        raise StoreError(f"工具「{tool['name']}」当前不可用", code="TOOL_DISABLED", status=409)
    requested_type = (data_type or tool["data_type"]).strip().upper()
    if requested_type != tool["data_type"]:
        raise StoreError(
            f"工具「{tool['name']}」要求 {tool['data_type']} 编号，收到 {requested_type}",
            code="DATA_TYPE_MISMATCH",
            status=400,
        )
    return tool, requested_type


def _validate_reason(tool: dict, reason_code: Optional[int], reason_detail: str) -> tuple[Optional[int], str]:
    options = tool.get("options") or []
    if not options:
        return None, ""
    if reason_code is None:
        raise StoreError(f"请选择{tool['option_title'] or '选项'}", code="ARGS", status=400)
    if not any(option["code"] == reason_code for option in options):
        raise StoreError("所选选项不在服务端下发的范围内", code="ARGS", status=400)
    if reason_code == REASON_NEED_INPUT and not (reason_detail or "").strip():
        raise StoreError("选择「其他」时必须填写具体原因", code="ARGS", status=400)
    return reason_code, (reason_detail or "").strip()[:MAX_REASON_DETAIL]


# ==================== 创建 / 发送 ====================
def create_action(
    user: dict,
    *,
    tool_id: str = "",
    tool_name: str = "",
    data_type: str = "",
    data_id: str = "",
    reason_code: Optional[int] = None,
    reason_detail: str = "",
    parent_msg_id: Optional[str] = None,
    account: Optional[str] = None,
    tool: Optional[dict] = None,
    batch_id: Optional[str] = None,
) -> dict:
    """创建一次写操作预览（只读上游：查商品 + 拦截检查 + 组装报文，不发送）。

    `account` / `tool` / `batch_id` 供批量执行器复用，避免每项重复查询会话与工具清单。
    """
    ensure_writes_enabled()
    user_id = user["id"]
    account = account or require_account(user_id)
    account_masked = (get_temu_session(user_id) or {}).get("account_masked", "")

    if tool is None:
        tool, requested_type = _resolve_tool(user_id, account, tool_id, tool_name, data_type)
    else:
        requested_type = (data_type or tool["data_type"]).strip().upper()
        if requested_type != tool["data_type"]:
            raise StoreError(
                f"工具「{tool['name']}」要求 {tool['data_type']} 编号，收到 {requested_type}",
                code="DATA_TYPE_MISMATCH",
                status=400,
            )

    identifier_value = _text(data_id).strip()
    if not identifier_value.isdigit():
        raise StoreError("商品 ID 必须是纯数字", code="ARGS", status=400)

    reason_code, reason_detail = _validate_reason(tool, reason_code, reason_detail)

    # 查商品（服务端硬校验 name/img 非空，查不到就不要往下走）
    try:
        product = store_kefu.query_product(user_id, requested_type, identifier_value, tool["tool_id"])
    except StoreError as exc:
        _record_failure(user_id, tool, identifier_value, reason_code, reason_detail, exc.message,
                        batch_id=batch_id, account_masked=account_masked)
        raise
    if not product.get("ok") or not product.get("product_name") or not product.get("product_picture"):
        message = "查不到该商品的名称或主图，服务端会拒收（请核对 ID 与数据类型）"
        _record_failure(user_id, tool, identifier_value, reason_code, reason_detail, message,
                        batch_id=batch_id, account_masked=account_masked)
        raise StoreError(message, code="PRODUCT_INCOMPLETE", status=409)

    # 拦截检查（★ 有状态，只调这一次；结果落库，发送阶段复用）
    try:
        intercept = temu_bridge.kefu_intercept(account, identifier_value, tool["tool_id"])
    except BridgeError as exc:
        error = translate_bridge_error(exc)
        _record_failure(user_id, tool, identifier_value, reason_code, reason_detail, error.message,
                        batch_id=batch_id, account_masked=account_masked)
        raise error from None

    passed = bool(intercept.get("passed"))
    message = ""
    if not passed:
        raw = intercept.get("interceptMsg")
        if isinstance(raw, dict):
            raw = " / ".join(str(part) for part in (raw.get("hint"), raw.get("subHint")) if part)
        message = _text(raw) or f"拦截检查未通过（interceptCode={intercept.get('interceptCode')}）"

    # 工具卡片（parentMsgId 必须来自卡片消息，不能用上一条聊天消息）
    parent = _text(parent_msg_id)
    if not parent:
        try:
            # 顺序：翻页找现成卡片 → 找不到就发工具名文本唤醒（保证上下文新鲜）
            card_data = store_kefu.ensure_card(
                user_id,
                tool_name=tool["name"],
                tool_id=tool["tool_id"],
            )
            parent = _text(card_data["msg_id"])
        except StoreError as exc:
            # 卡片只在会话早期推送，之后会被大量提交/受理消息挤出「最近 50 条」窗口。
            # parentMsgId 在会话内可长期复用，所以回退到最近一次成功提交用过的值。
            fallback = latest_kefu_parent_msg_id(account_masked, tool["tool_id"])
            if fallback and passed:
                parent = fallback
                logger.info(
                    "kefu card missing, fallback parentMsgId=%s tool=%s", fallback, tool["tool_id"]
                )
            else:
                hint = "未找到自助工具卡片，请先在 Temu 后台点开一次「联系官方客服」建立会话"
                _record_failure(user_id, tool, identifier_value, reason_code, reason_detail,
                                message or hint, batch_id=batch_id, account_masked=account_masked)
                if not passed:
                    raise
                raise StoreError(hint, code="CARD_EMPTY", status=409) from exc
    if not parent:
        raise StoreError("工具卡片缺少 msgId，请刷新后重试", code="CARD_EMPTY", status=409)

    data_type_code = tool["data_id_type"] or 1
    content = _build_content(tool, product, data_type_code, identifier_value, reason_code, reason_detail)
    request_body = {
        "parentMsgId": int(parent) if parent.isdigit() else parent,
        "contentType": CONTENT_TYPE_TOOL_SUBMIT,
        "content": json.dumps(content, ensure_ascii=False),
    }

    action = create_kefu_action(
        uuid.uuid4().hex,
        user_id=user_id,
        batch_id=batch_id,
        account_masked=account_masked,
        tool_id=tool["tool_id"],
        tool_name=tool["name"],
        data_type=data_type_code,
        data_type_name=tool["data_type"],
        data_id=identifier_value,
        reason_code=reason_code,
        reason_detail=reason_detail,
        stage="preview" if passed else "failed",
        preview=content,
        product_snapshot={
            "product_name": product.get("product_name") or "",
            "product_picture": product.get("product_picture") or "",
            "product_skc_id": product.get("product_skc_id") or "",
            "product_id": product.get("product_id") or "",
        },
        parent_msg_id=parent,
        intercept_code=intercept.get("interceptCode"),
        intercept_message=_text(message)[:255],
        request_body=request_body,
        error=_text(message)[:255] if not passed else "",
    )

    if not passed:
        raise StoreError(f"拦截检查未通过：{message}", code="INTERCEPTED", status=409)

    return _finalize(action)


# 不可重试的失败原因（拦截未通过 / 查不到商品 / 报文缺失 —— 重发也没用）
_NON_RETRYABLE_HINTS = ("拦截检查未通过", "查不到", "报文缺失", "不存在", "未找到")


def _retryable(action: dict) -> bool:
    """失败的 action 是否值得重发（平台临时错误可以，业务性错误不行）。"""
    error = str(action.get("error") or "")
    if not error:
        return False
    return not any(hint in error for hint in _NON_RETRYABLE_HINTS)


def get_last_sent_at(account: str) -> Optional[datetime]:
    """取该账号最近一次提交时间（用于隐形限流保护）。

    SQLite 读出来的 datetime 不带时区，这里统一补成 UTC，否则和 `_now()` 相减会抛
    `can't subtract offset-naive and offset-aware datetimes`。
    """
    raw = latest_kefu_sent_at(mask_account(account))
    if not raw:
        return None
    return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)


def _ensure_fresh_parent(account: str, action: dict) -> str:
    """取一张「够新」的工具卡片 msgId（走 store_kefu 的卡片缓存，避免重复翻页/唤醒）。

    提交前必须做：卡片上下文过期时平台会把提交丢进异步队列（回执「加速等待序列」），
    或者直接回「系统忙碌」。
    """
    tool_name = action.get("tool_name") or ""
    if not tool_name:
        return ""
    try:
        data = store_kefu.ensure_card(
            action["user_id"],
            tool_name=tool_name,
            tool_id=action.get("tool_id"),
            max_age_ms=SUBMIT_CARD_MAX_AGE_MS,
        )
        return _text(data.get("msg_id"))
    except StoreError as exc:
        logger.info(
            "kefu ensure failed for action=%s: %s", _text(action.get("id"))[:8], exc.message
        )
        return ""


def _send_action(account: str, action: dict, *, enforce_interval: bool = True) -> dict:
    """把已预览的 action 真正发给平台（幂等：已发送直接返回）。

    `enforce_interval=False` 供批量执行器使用：批量自己按 MIN_SUBMIT_INTERVAL 控速，
    这里再卡一次会让每一项都撞上自己的限流。
    """
    action_id = action["id"]
    if action["stage"] in ("sent", "replied"):
        return _finalize(action)
    if action["stage"] == "rejected":
        raise StoreError(
            action["error"] or "平台拒绝了该操作，请人工处理",
            code="ACTION_REJECTED",
            status=409,
        )
    if action["stage"] == "failed" and not _retryable(action):
        raise StoreError(action["error"] or "该操作不可重试，请重新创建", code="ACTION_FAILED", status=409)

    body = action["request_body"] or {}
    if not body.get("content"):
        update_kefu_action(action_id, stage="failed", error="操作报文缺失", finished_at=_now())
        raise StoreError("操作报文缺失，请重新创建", code="ACTION_BROKEN", status=409)

    # 同账号提交间隔保护（平台有隐形限流，过密会全部返回「系统忙碌」）
    if enforce_interval:
        last_sent_at = get_last_sent_at(account)
        if last_sent_at:
            elapsed = (_now() - last_sent_at).total_seconds()
            if elapsed < MIN_SUBMIT_INTERVAL:
                raise StoreError(
                    f"提交过于频繁，请 {int(MIN_SUBMIT_INTERVAL - elapsed)} 秒后再试",
                    code="TOO_FREQUENT",
                    status=429,
                )

    snapshot = action["product_snapshot"] or {}
    # 降级重试 / 缺少 parentMsgId 时，先确保会话里有「新鲜卡片上下文」，
    # 否则平台会把这次提交丢进异步队列（回执「已进入加速等待序列」→ 系统忙碌）。
    if action["stage"] in ("retry_pending", "degraded") or not _text(action.get("parent_msg_id")):
        fresh_parent = _ensure_fresh_parent(account, action)
        if fresh_parent:
            action = dict(action)
            action["parent_msg_id"] = fresh_parent

    payload = {
        "toolId": action["tool_id"],
        "toolName": action["tool_name"],
        "reason": action["reason_code"],
        "detail": action["reason_detail"],
        "parentMsgId": action["parent_msg_id"],
        # 复用创建时已消费的商品与拦截结果：bridge 会原样传给 kefu.submit，
        # 从而跳过内部的 queryProduct + preIntercept（否则会触发「请勿重复咨询」）
        "product": {
            "ok": True,
            "productName": snapshot.get("product_name", ""),
            "productPicture": snapshot.get("product_picture", ""),
        },
        "intercept": {"ok": True, "interceptCode": 0, "passed": True},
    }
    if action["data_type_name"] == "SKU":
        payload["skuId"] = action["data_id"]
    elif action["data_type_name"] == "SPU":
        payload["spuId"] = action["data_id"]
    else:
        payload["skcId"] = action["data_id"]

    try:
        result = temu_bridge.kefu_submit(account, payload, dry_run=False)
    except BridgeError as exc:
        error = translate_bridge_error(exc)
        update_kefu_action(action_id, stage="failed", error=error.message[:255], finished_at=_now())
        raise error from None

    if not result.get("ok"):
        message = _text(result.get("err") or result.get("msg") or "提交失败")
        update_kefu_action(
            action_id,
            stage="failed",
            error=message[:255],
            finished_at=_now(),
            response={"msg": message, "code": result.get("code")},
        )
        raise StoreError(message, code="SUBMIT_FAILED", status=502)

    updated = update_kefu_action(
        action_id,
        stage="sent",
        msg_id=_text(result.get("msgId")),
        sent_at=_now(),
        next_retry_at=None,
        parent_msg_id=action.get("parent_msg_id") or "",
        response={key: value for key, value in result.items() if key not in ("sent", "product")},
    )
    return _finalize(updated or action)


def _resend_action(account: str, action: dict, *, enforce_interval: bool = True) -> dict:
    """重试提交：复用创建阶段的 request_body，**不重新查商品、不重新拦截**。

    `preIntercept` 有状态（同一商品只能成功一次），重试时重新拦截必然失败，
    所以统一走 `_send_action`（它只发已存的报文）。
    """
    ensure_writes_enabled()
    return _send_action(account, action, enforce_interval=enforce_interval)


def confirm_action(user: dict, action_id: str) -> dict:
    ensure_writes_enabled()
    action = get_kefu_action(action_id)
    if not action or action["user_id"] != user["id"]:
        raise StoreError("操作记录不存在", code="NOT_FOUND", status=404)
    account = require_account(user["id"])
    return _send_action(account, action)


# ==================== 回执 ====================
def fetch_reply(user: dict, action_id: str) -> dict:
    """拉机器人回执（contentType=12 为终态）。幂等：已终态直接返回。"""
    action = get_kefu_action(action_id)
    if not action or action["user_id"] != user["id"]:
        raise StoreError("操作记录不存在", code="NOT_FOUND", status=404)
    return refresh_reply(action, require_account(user["id"]))


def _parse_iso(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _reply_by_time_window(messages: list[dict], action: dict) -> Optional[dict]:
    """严格过滤失效时的兜底：按「发送时间之后」的机器人消息认领回执。

    为什么需要：机器人回执的 msgId 不保证大于提交消息的 msgId（实测两条连续提交时，
    回执 ID 落在两次提交之间），用 `msgId > sinceMsgId` 过滤会把它漏掉。

    为什么不能取最早一条：平台对一次提交会发多条消息 ——
    先「已进入加速等待序列」（t+3s），几分钟后才是「操作失败/已下架」（t+4min）。
    取最早只会永远停在受理话术上，所以按优先级挑。
    """
    sent_at = _parse_iso(action.get("sent_at"))
    if not sent_at:
        return None
    threshold = int(sent_at.timestamp() * 1000) - 1000
    candidates: list[tuple[int, dict]] = []
    for message in messages or []:
        content_type = message.get("content_type")
        if content_type not in (1, 12):
            continue
        raw_ts = message.get("timestamp")
        if not isinstance(raw_ts, (int, float)):
            continue
        ts_ms = int(raw_ts if raw_ts > 1e12 else raw_ts * 1000)
        if ts_ms >= threshold:
            candidates.append((ts_ms, message))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])

    def _pick(items: list[tuple[int, dict]]) -> dict:
        # 1) 终态优先
        for _, msg in items:
            if msg.get("content_type") == TERMINAL_CONTENT_TYPE:
                return msg
        # 2) 失败 / 拒绝话术
        for _, msg in items:
            text = str(msg.get("content") or "")
            if any(hint in text for hint in FAILED_REPLY_HINTS):
                return msg
            if any(hint in text for hint in REJECTED_REPLY_HINTS):
                return msg
        # 3) 兜底取最新一条（可能仍是受理话术，交给后续轮询继续等）
        return items[-1][1]

    hit = _pick(candidates)
    return {
        "contentType": hit.get("content_type"),
        "msgId": hit.get("msg_id"),
        "content": hit.get("content") or "",
        "source": "time-window",
    }


def _pick_reply_from_replies(replies: list[dict]) -> Optional[dict]:
    """在 /kefu/reply 的结果里挑一条最有信息量的：终态 > 失败/拒绝 > 最新。"""
    if not replies:
        return None
    terminal = next((item for item in replies if item.get("contentType") == TERMINAL_CONTENT_TYPE), None)
    if terminal:
        return terminal
    bad = next(
        (
            item
            for item in replies
            if any(
                hint in str(item.get("content") or "")
                for hint in FAILED_REPLY_HINTS + REJECTED_REPLY_HINTS
            )
        ),
        None,
    )
    return bad or replies[-1]


def refresh_reply(action: dict, account: str) -> dict:
    """供单条与批量共用的回执刷新（account 由调用方提供）。

    两级策略：
    1. 严格：按 `sinceMsgId` 只取本条提交之后的消息（正常情况）；
    2. 宽松：严格为空时，按发送时间窗认领（连续提交时 msgId 可能乱序）。

    结果分类见 `classify_reply`；失败会自动排队重试（`retry_pending`）。
    """
    if action["stage"] not in ("sent", "accepted", "unknown", "replied"):
        return _finalize(action)
    # 已拿到 contentType=12 的终态记录不再重复拉；历史上被误标的受理话术允许重新判定一次
    existing = action.get("robot_reply") or {}
    if action["stage"] == "replied" and existing.get("contentType") == TERMINAL_CONTENT_TYPE:
        return _finalize(action)

    final: Optional[dict] = None
    try:
        reply = temu_bridge.kefu_reply(account, action.get("msg_id") or None)
        final = _pick_reply_from_replies(reply.get("replies") or [])
    except BridgeError as exc:
        raise translate_bridge_error(exc) from None

    if final is None:
        try:
            messages = temu_bridge.kefu_messages(account, limit=50)
            final = _reply_by_time_window(messages.get("items") or [], action)
        except BridgeError:
            final = None

    if final is None:
        return _finalize(action)

    stage = classify_reply(final)
    fields: dict = {"stage": stage, "robot_reply": final}

    if stage == "replied":
        fields["finished_at"] = _now()
    elif stage == "degraded":
        # 降级：这次提交没进同步执行器，唤醒卡片后尽快重试（不用等 5 分钟）
        retry_count = int(action.get("retry_count") or 0)
        fields["error"] = "平台降级处理（未进入同步执行器），将唤醒卡片后重试"[:255]
        if retry_count < MAX_RETRY:
            fields["stage"] = "retry_pending"
            fields["retry_count"] = retry_count + 1
            fields["next_retry_at"] = _now() + timedelta(seconds=DEGRADED_RETRY_DELAY)
        else:
            fields["stage"] = "failed"
            fields["error"] = "多次降级处理，已停止重试（请在 Temu 后台手动点开一次「联系官方客服 → 自助工具」）"[:255]
            fields["finished_at"] = _now()
    elif stage in ("failed", "rejected"):
        message = _text(final.get("content"))[:255]
        fields["finished_at"] = _now()
        fields["error"] = message
        if stage == "failed":
            # 平台临时错误：排队重试（复用已存报文，不重新拦截）
            retry_count = int(action.get("retry_count") or 0)
            if retry_count < MAX_RETRY:
                delay = RETRY_BACKOFF[min(retry_count, len(RETRY_BACKOFF) - 1)]
                fields["stage"] = "retry_pending"
                fields["retry_count"] = retry_count + 1
                fields["next_retry_at"] = _now() + timedelta(seconds=delay)
                fields["finished_at"] = None
                fields["error"] = f"{message}（已排队重试，第 {retry_count + 1} 次）"[:255]

    updated = update_kefu_action(action["id"], **fields)
    return _finalize(updated or action)


# ==================== 查询 ====================
def list_actions(user: dict, *, limit: int = 30, stage: Optional[str] = None) -> list[dict]:
    items = list_kefu_actions(limit=limit, user_id=user["id"], stage=stage)
    return [_finalize(item) for item in items]


def get_action(user: dict, action_id: str) -> dict:
    action = get_kefu_action(action_id)
    if not action or action["user_id"] != user["id"]:
        raise StoreError("操作记录不存在", code="NOT_FOUND", status=404)
    return _finalize(action)


def list_all_actions(
    *,
    limit: int = 100,
    stage: Optional[str] = None,
    keyword: str = "",
    start=None,
    end=None,
) -> list[dict]:
    """管理员视图：跨用户的写操作审计（补上操作人信息）。"""
    from app.database import list_users

    items = list_kefu_actions(limit=limit, stage=stage, keyword=keyword, start=start, end=end)
    owners = {user["id"]: user for user in list_users()}
    for item in items:
        owner = owners.get(item["user_id"]) or {}
        item["username"] = owner.get("username", "")
        item["display_name"] = owner.get("display_name", "")
    return items
