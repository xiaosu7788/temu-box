"""客服写操作失败自动重试调度器。

背景：平台对提交过密会返回「系统忙碌中，请您稍后重试」（临时性错误），
这类失败值得自动重试；业务性失败（拦截未通过、商品不存在）不重试。

约束：
- **绝不重新拦截**：重试复用创建阶段存下的 `request_body`（`preIntercept` 有状态，
  同一商品只能成功一次）。
- **控速**：每轮只处理 1 条，tick 60 秒 > `MIN_SUBMIT_INTERVAL`，避免自己撞自己的限流。
- 可在系统设置里用 `kefu_auto_retry_enabled` 关闭。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from app.database import get_temu_session, list_kefu_retry_candidates
from app.services import store_kefu_actions, system
from app.services.store_session import StoreError

logger = logging.getLogger("temubox.store.retry")

TICK_SECONDS = 60
BATCH = 1


def _account_for(action: dict) -> str:
    user_id = action.get("user_id")
    if not user_id:
        return ""
    session = get_temu_session(user_id)
    if not session or session["status"] != "active" or not session["account"]:
        return ""
    return session["account"]


def run_once(now: datetime | None = None) -> int:
    """处理一轮到期重试，返回本次真正重发的条数。"""
    try:
        settings = system.get()
    except Exception:  # noqa: BLE001
        return 0
    if not settings.get("kefu_auto_retry_enabled", True):
        return 0
    if not settings.get("kefu_writes_enabled", True):
        return 0

    moment = now or datetime.now(timezone.utc)
    candidates = list_kefu_retry_candidates(moment, limit=BATCH)
    done = 0
    for action in candidates:
        account = _account_for(action)
        if not account:
            from app.database import update_kefu_action

            update_kefu_action(
                action["id"],
                stage="failed",
                error="店铺会话已失效，无法自动重试（请重新登录店铺后手动重试）"[:255],
                finished_at=datetime.now(timezone.utc),
                next_retry_at=None,
            )
            continue
        try:
            store_kefu_actions._resend_action(account, action)
            done += 1
            logger.info("kefu retry ok action=%s data=%s", action["id"][:8], action["data_id"])
        except StoreError as exc:
            # 限流 / 会话失效等：保留 retry_pending 等下一轮；业务失败已在 _send_action 里落 failed
            logger.info("kefu retry deferred action=%s: %s", action["id"][:8], exc.message)
        except Exception as exc:  # noqa: BLE001 - 单条异常不能中断调度
            logger.exception("kefu retry crashed action=%s: %s", action["id"][:8], exc)
    return done


class RetryScheduler(threading.Thread):
    def __init__(self) -> None:
        super().__init__(name="kefu-retry", daemon=True)
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        logger.info("kefu retry scheduler started (tick=%ss)", TICK_SECONDS)
        while not self._stop.wait(TICK_SECONDS):
            try:
                run_once()
            except Exception:  # noqa: BLE001
                logger.exception("kefu retry tick failed")


scheduler = RetryScheduler()
