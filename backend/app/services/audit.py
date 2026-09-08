"""Audit logging for security-relevant and administrative operations."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Request

from app.database import distinct_audit_actions, insert_audit_log, list_audit_logs

logger = logging.getLogger("temubox.audit")


def record(
    actor: Optional[dict],
    action: str,
    target_type: str = "",
    target_id: object = "",
    detail: str = "",
    request: Optional[Request] = None,
) -> None:
    """记录一条审计日志。审计写入失败不应影响主流程。"""
    try:
        ip = ""
        if request is not None and request.client is not None:
            ip = request.client.host or ""
        insert_audit_log(
            actor_id=actor.get("id") if actor else None,
            actor_username=(actor.get("username") if actor else "") or "",
            action=action,
            target_type=target_type,
            target_id=str(target_id or ""),
            detail=detail,
            ip=ip,
        )
    except Exception:
        logger.exception("Failed to write audit log: %s", action)


def query(page: int = 1, page_size: int = 30, action: str = "", actor_id: Optional[int] = None, keyword: str = "") -> dict:
    return list_audit_logs(page=page, page_size=page_size, action=action, actor_id=actor_id, keyword=keyword)


def actions() -> list[str]:
    return distinct_audit_actions()
