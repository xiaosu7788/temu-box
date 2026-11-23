"""客服工具的批量执行：一批编号、一个工具、一次确认。

流程（两阶段）：
1. **查询**（只读）：前端调 `/kefu/query-batch` 把编号换成商品详情列表，可反复执行；
2. **执行**（写）：`start_batch` 建任务并起后台线程，逐个
   `查商品 → 拦截检查 → 组装报文 → 发送 → 拉回执`，前端轮询 `GET /batches/{id}` 看进度。

为什么放后端线程而不是前端循环：
- 50 个编号要 100+ 次上游请求（query + intercept），同步 HTTP 会超时；
- 前端刷新/关页不该丢掉进度，且失败记录要进审计；
- 每项串行 + 间隔，避免撞上拦截检查的有状态限制与工具限流。
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.database import (
    create_kefu_batch,
    get_kefu_action,
    get_kefu_batch,
    get_temu_session,
    list_kefu_actions_by_batch,
    update_kefu_batch,
)
from app.services import store_kefu, store_kefu_actions
from app.services.store_goods import require_account
from app.services.store_kefu_actions import (
    _resolve_tool,
    _send_action,
    _validate_reason,
    create_action,
    ensure_writes_enabled,
    refresh_reply,
)
from app.services.store_session import StoreError

logger = logging.getLogger("temubox.store.batch")

# 编号解析与上限复用 store_kefu 的实现（前后端对「一批多少个」只有一个口径）
MAX_BATCH_IDS = store_kefu.MAX_BATCH_IDS
normalize_ids = store_kefu.normalize_ids
# 同账号提交间隔：平台有隐形限流，过密会全部返回「系统忙碌」。
# 批量必须按这个节奏跑（50 个约 25 分钟），否则整批都会失败。
ITEM_DELAY_SECONDS = store_kefu_actions.MIN_SUBMIT_INTERVAL
# 回执轮询：同步路径 2~3 秒就出终态，所以每项提交后立刻短等再拉一次，
# 逐条实时出结果；批尾只留少量轮次兜底异步结果。
REPLY_INLINE_WAIT = 2.0
REPLY_DELAY_SECONDS = 2.0
REPLY_ROUNDS = 2


def _item_view(action: dict) -> dict:
    snapshot = action.get("product_snapshot") or {}
    return {
        "action_id": action["id"],
        "data_id": action["data_id"],
        "data_type_name": action["data_type_name"],
        "stage": action["stage"],
        "product_name": snapshot.get("product_name", ""),
        "product_picture": snapshot.get("product_picture", ""),
        "msg_id": action["msg_id"],
        "error": action["error"],
        "intercept_code": action["intercept_code"],
        "robot_reply": action["robot_reply"],
        "created_at": action["created_at"],
        "finished_at": action["finished_at"],
    }


def batch_view(user: dict, batch_id: str) -> dict:
    batch = get_kefu_batch(batch_id)
    if not batch or batch["user_id"] != user["id"]:
        raise StoreError("批量任务不存在", code="NOT_FOUND", status=404)
    actions = list_kefu_actions_by_batch(batch_id)
    items = [_item_view(action) for action in actions]
    finished = len(items)
    return {
        "batch": batch,
        "items": items,
        "progress": {
            "total": batch["total"],
            "processed": finished,
            "succeeded": batch["succeeded"],
            "failed": batch["failed"],
            "pending": max(0, batch["total"] - finished),
            # 已发送但还没拿到最终结果的数量（含只回了「已受理」话术的）
            "awaiting_reply": sum(1 for item in items if item["stage"] in ("sent", "accepted")),
        },
    }


def list_batches(user: dict, *, limit: int = 20) -> list[dict]:
    from app.database import list_kefu_batches

    return list_kefu_batches(user_id=user["id"], limit=limit)


def start_batch(
    user: dict,
    *,
    tool_id: str = "",
    tool_name: str = "",
    data_type: str = "",
    ids=None,
    reason_code: Optional[int] = None,
    reason_detail: str = "",
) -> dict:
    """校验并入队；立即返回 batch 视图（status=queued）。"""
    ensure_writes_enabled()
    user_id = user["id"]
    account = require_account(user_id)
    tool, _ = _resolve_tool(user_id, account, tool_id, tool_name, data_type)
    reason_code, reason_detail = _validate_reason(tool, reason_code, reason_detail)

    parsed = normalize_ids(ids)
    if not parsed:
        raise StoreError("请粘贴至少一个纯数字编号", code="ARGS", status=400)
    if len(parsed) >= MAX_BATCH_IDS:
        logger.info("batch ids truncated to %s for user=%s", MAX_BATCH_IDS, user_id)

    batch = create_kefu_batch(
        uuid.uuid4().hex,
        user_id=user_id,
        account_masked=(get_temu_session(user_id) or {}).get("account_masked", ""),
        tool_id=tool["tool_id"],
        tool_name=tool["name"],
        data_type_name=tool["data_type"],
        reason_code=reason_code,
        reason_detail=reason_detail,
        status="queued",
        total=len(parsed),
    )

    _spawn(_run_batch, (batch["id"], user_id, account, tool, parsed, reason_code, reason_detail), f"kefu-batch-{batch['id'][:8]}")
    return batch_view(user, batch["id"])


def _spawn(target, args: tuple, name: str) -> None:
    """后台执行入口（测试里替换成同步执行，避免 sleep + 轮询）。"""
    thread = threading.Thread(target=target, args=args, name=name, daemon=True)
    thread.start()


def cancel_batch(user: dict, batch_id: str) -> dict:
    batch = get_kefu_batch(batch_id)
    if not batch or batch["user_id"] != user["id"]:
        raise StoreError("批量任务不存在", code="NOT_FOUND", status=404)
    if batch["status"] in ("done", "cancelled"):
        return batch_view(user, batch_id)
    update_kefu_batch(batch_id, status="cancelled")
    return batch_view(user, batch_id)


def _run_batch(
    batch_id: str,
    user_id: int,
    account: str,
    tool: dict,
    ids: list[str],
    reason_code: Optional[int],
    reason_detail: str,
) -> None:
    update_kefu_batch(batch_id, status="running", started_at=datetime.now(timezone.utc))
    succeeded = 0
    failed = 0
    principal = {"id": user_id}

    for data_id in ids:
        current = get_kefu_batch(batch_id)
        if not current or current["status"] == "cancelled":
            break
        try:
            action = create_action(
                principal,
                tool=tool,
                account=account,
                data_id=data_id,
                reason_code=reason_code,
                reason_detail=reason_detail,
                batch_id=batch_id,
            )
            _send_action(account, action, enforce_interval=False)
            succeeded += 1
        except StoreError as exc:
            failed += 1
            logger.info("batch %s item %s failed: %s", batch_id, data_id, exc.message)
        except Exception as exc:  # noqa: BLE001 - 单条异常不能中断整批
            failed += 1
            logger.exception("batch %s item %s crashed: %s", batch_id, data_id, exc)
        update_kefu_batch(batch_id, succeeded=succeeded, failed=failed)

        # 提交后立刻短等并拉这一条的回执：同步路径 2~3 秒就有终态，
        # 不能等整批跑完再统一拉（否则第 1 项要等 N×间隔 才显示结果）。
        if REPLY_INLINE_WAIT > 0:
            time.sleep(REPLY_INLINE_WAIT)
            try:
                latest = get_kefu_action(action["id"])
                if latest and latest["stage"] == "sent":
                    refresh_reply(latest, account)
            except StoreError as exc:
                logger.info("batch %s inline reply failed for %s: %s", batch_id, data_id, exc.message)
            except Exception as exc:  # noqa: BLE001
                logger.exception("batch %s inline reply crashed for %s: %s", batch_id, data_id, exc)

        # 内联等待已占用一部分间隔，剩余部分补齐即可，不额外拉长总时长
        time.sleep(max(0.0, ITEM_DELAY_SECONDS - REPLY_INLINE_WAIT))

    update_kefu_batch(batch_id, succeeded=succeeded, failed=failed)
    _collect_replies(batch_id, account)

    final = get_kefu_batch(batch_id)
    if final and final["status"] == "cancelled":
        return
    update_kefu_batch(batch_id, status="done", succeeded=succeeded, failed=failed,
                      finished_at=datetime.now(timezone.utc))


def _collect_replies(batch_id: str, account: str) -> None:
    """机器人回执是异步的：延迟后多轮重试，而不是发完就拉一次。

    实测：连续提交两条时，第二条的回执可能几秒后才到，甚至其 msgId 小于提交消息的
    msgId（由 refresh_reply 的时间窗兜底认领）。
    """
    rounds = max(1, int(REPLY_ROUNDS))
    # accepted = 平台只回了「已受理」话术，终态还没来，后续轮次继续拉
    pending_stages = ("sent", "accepted")
    for round_index in range(rounds):
        pending = [action for action in list_kefu_actions_by_batch(batch_id) if action["stage"] in pending_stages]
        if not pending:
            return
        time.sleep(REPLY_DELAY_SECONDS)
        for action in pending:
            if get_kefu_batch(batch_id)["status"] == "cancelled":
                return
            try:
                refresh_reply(action, account)
            except StoreError as exc:
                logger.info("batch %s reply failed for %s: %s", batch_id, action["data_id"], exc.message)
            except Exception as exc:  # noqa: BLE001
                logger.exception("batch %s reply crashed for %s: %s", batch_id, action["data_id"], exc)
        if round_index == rounds - 1:
            remaining = [a for a in list_kefu_actions_by_batch(batch_id) if a["stage"] in pending_stages]
            if remaining:
                logger.info("batch %s leaves %s actions awaiting final reply", batch_id, len(remaining))
