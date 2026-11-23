"""Runtime system settings: task pool limits, cleanup and audit retention."""
from __future__ import annotations

from app.config import TASK_WORKERS
from app.database import get_system_settings, save_system_settings

DEFAULTS = {
    "task_workers": TASK_WORKERS,
    "task_queue_limit": 50,
    "activity_workers": TASK_WORKERS,
    "activity_queue_limit": 50,
    "cleanup_enabled": True,
    "cleanup_retention_days": 30,
    "audit_retention_days": 180,
    # 店铺模块设置
    "kefu_writes_enabled": True,          # 客服写操作总熔断（关闭 = 全员只读）
    "kefu_auto_retry_enabled": True,      # 平台「系统忙碌」是否自动重试
    "store_login_log_retention_days": 90,  # 店铺登录日志保留天数
    "kefu_action_retention_days": 180,     # 客服操作审计保留天数
}

BOOL_KEYS = {"cleanup_enabled", "kefu_writes_enabled", "kefu_auto_retry_enabled"}

LIMITS = {
    "store_login_log_retention_days": (7, 3650),
    "kefu_action_retention_days": (7, 3650),
    "task_workers": (1, 16),
    "task_queue_limit": (1, 1000),
    "activity_workers": (1, 16),
    "activity_queue_limit": (1, 1000),
    "cleanup_retention_days": (1, 3650),
    "audit_retention_days": (7, 3650),
}


def get() -> dict:
    return get_system_settings(DEFAULTS)


def validate(payload: dict) -> dict:
    # 从「当前生效值」出发，而不是 DEFAULTS：
    # 否则调用方漏传某个字段时会把用户已经改过的值重置回默认
    result = get()
    for key, value in payload.items():
        if key not in result:
            continue
        if key in BOOL_KEYS:
            result[key] = bool(value)
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} 必须是整数") from None
        low, high = LIMITS[key]
        if not low <= number <= high:
            raise ValueError(f"{key} 必须在 {low}-{high} 之间")
        result[key] = number
    return result


def update(payload: dict) -> dict:
    settings = validate(payload)
    save_system_settings(settings)
    return settings
