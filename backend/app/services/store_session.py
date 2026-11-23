"""店铺会话：一用户一店铺的登录、退出、续期与凭据管理。

产品约定（已确认）：
- 每个用户登录自己的 Temu 店铺，一用户只能有一个活跃店铺；
- 换店铺必须先退出（不做多店铺切换器）；
- 同一 Temu 账号全系统只能有一个活跃会话（重复登录会互相顶号）；
- 密码默认不入库；用户勾选「记住密码」后以 AES（HMAC-CTR）密文保存；
- 会话失效不自动重登（自动重登会消耗风控配额，甚至把图形验证码冷却刷新得更久）。
"""
from __future__ import annotations

import re

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.config import STORE_SESSION_HOURS
from app.database import (
    add_temu_login_log,
    clear_temu_cooldown,
    clear_temu_session,
    delete_temu_credential,
    find_temu_session_by_account,
    get_temu_credential,
    get_temu_session,
    list_temu_sessions,
    list_users,
    save_temu_credential,
    upsert_temu_session,
)
from app.services import credential_cipher, store_accounts, temu_bridge
from app.services.temu_bridge import BridgeError
import logging

logger = logging.getLogger("temubox.store_session")

CAPTCHA_COOLDOWN = timedelta(minutes=30)


class StoreError(Exception):
    """可直接翻译成 HTTP 错误（路由层统一处理）。"""

    def __init__(self, message: str, *, code: str = "ERR", status: int = 400, retry_after: Optional[int] = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.retry_after = retry_after


# ==================== 基础工具 ====================
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def account_hash(account: str) -> str:
    """与 bridge 的 accountKey 等价（sha1(trim+lower) 前 16 位）。"""
    return hashlib.sha1(str(account or "").strip().lower().encode("utf-8")).hexdigest()[:16]


def mask_account(account: str) -> str:
    text = str(account or "").strip()
    if not text:
        return ""
    if "@" in text:
        name, domain = text.split("@", 1)
        return (name[:1] or "") + "***@" + domain
    if len(text) >= 7:
        return text[:3] + "****" + text[-2:]
    return (text[:1] or "") + "***"


def _parse_instant(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def cooldown_seconds(value) -> int:
    instant = _parse_instant(value)
    if not instant:
        return 0
    return max(0, int((instant - now_utc()).total_seconds()))


def _view_from_row(row: Optional[dict]) -> dict:
    if not row:
        return {
            "status": "logged_out",
            "account_masked": "",
            "mall_id": None,
            "mall_name": "",
            "mall_list": [],
            "login_at": None,
            "expires_at": None,
            "cooldown_until": None,
            "cooldown_seconds": 0,
            "last_error": "",
            "last_error_code": "",
            "remember": False,
            "remember_account": "",
        }
    return {
        "status": row["status"],
        "account_masked": row["account_masked"],
        "mall_id": row["mall_id"],
        "mall_name": row["mall_name"],
        "mall_list": row["mall_list"] or [],
        "login_at": row["login_at"],
        "expires_at": row["expires_at"],
        "cooldown_until": row["cooldown_until"],
        "cooldown_seconds": cooldown_seconds(row["cooldown_until"]),
        "last_error": row["last_error"],
        "last_error_code": row["last_error_code"],
        "remember": bool(row["remember"]),
        "remember_account": row["account_masked"] if row["remember"] else "",
    }


def _heal_cooldown(row: Optional[dict]) -> Optional[dict]:
    """冷却到期后把 status 从 'cooldown' 复位成 'logged_out'。

    否则状态会一直停在 cooldown：前端永久禁用登录按钮，倒计时却显示「0 分 00 秒」。
    """
    if not row or row["status"] != "cooldown":
        return row
    if cooldown_seconds(row["cooldown_until"]) > 0:
        return row
    try:
        clear_temu_cooldown(row["user_id"])
    except Exception:  # noqa: BLE001 - 自愈失败不该影响只读展示
        pass
    healed = dict(row)
    healed["status"] = "logged_out"
    healed["cooldown_until"] = None
    return healed


def view(user_id: int) -> dict:
    """给前端的会话视图（不含密码、不含账号原文）。"""
    data = _view_from_row(_heal_cooldown(get_temu_session(user_id)))
    data["bridge_configured"] = temu_bridge.configured()
    data["remember_available"] = credential_cipher.enabled()
    # 客服写操作的总熔断开关（管理员可在系统设置里一键切成只读）
    try:
        from app.services import system as system_settings

        data["writes_enabled"] = bool(system_settings.get().get("kefu_writes_enabled", True))
    except Exception:  # noqa: BLE001 - 读设置失败不该影响会话展示
        data["writes_enabled"] = True
    data["expires_in_seconds"] = max(
        0, int(((_parse_instant(data["expires_at"]) or now_utc()) - now_utc()).total_seconds())
    ) if data["status"] == "active" else 0
    return data


def user_binding(user_id: int) -> dict:
    """管理员视图：某个用户已绑定的店铺信息（账号脱敏，不含密码）。

    返回 `bound=False` 表示该用户从未绑定过店铺；否则给出会话状态、店铺名、
    登录/过期时间、冷却与最近错误，供后台排查用。
    """
    row = get_temu_session(user_id)
    if not row:
        return {"bound": False, "has_credential": bool(remembered_credentials(user_id))}
    item = _view_from_row(_heal_cooldown(row))
    item.pop("remember_account", None)
    return {
        "bound": True,
        "has_credential": bool(remembered_credentials(user_id)),
        **item,
        "last_seen_at": row.get("last_seen_at"),
        "updated_at": row.get("updated_at"),
    }


def list_all() -> list[dict]:
    """管理员视图：所有用户的店铺会话（账号一律脱敏）。"""
    users = {item["id"]: item for item in list_users()}
    items = []
    for row in list_temu_sessions():
        owner = users.get(row["user_id"]) or {}
        item = _view_from_row(_heal_cooldown(row))
        item.pop("remember_account", None)
        item.update({
            "user_id": row["user_id"],
            "username": owner.get("username", ""),
            "display_name": owner.get("display_name", ""),
            "store_access": bool(owner.get("store_access")),
        })
        items.append(item)
    return items


# ==================== 凭据（记住密码） ====================
def remembered_credentials(user_id: int) -> Optional[dict]:
    """只在服务端内部使用（一键重登），绝不返回给前端。"""
    if not credential_cipher.enabled():
        return None
    record = get_temu_credential(user_id)
    if not record or not record["ciphertext"]:
        return None
    try:
        password = credential_cipher.decrypt(record["ciphertext"])
    except credential_cipher.CredentialError:
        return None
    return {"account": record["account"], "password": password}


def clear_remembered(user_id: int) -> bool:
    return delete_temu_credential(user_id)


# ==================== 登录 / 退出 ====================
def translate_bridge_error(exc: BridgeError) -> StoreError:
    """bridge 错误 → 可直接映射 HTTP 的业务错误（登录、商品、客服共用）。"""
    code = str(exc.code or "")
    message = exc.message or "店铺服务返回异常"
    if code in ("54001", "CAPTCHA"):
        return StoreError("需要图形验证码，请 30 分钟后再试", code="COOLDOWN", status=429, retry_after=exc.retry_after or int(CAPTCHA_COOLDOWN.total_seconds()))
    if code == "RATE_LIMIT":
        return StoreError(message, code="RATE_LIMIT", status=429, retry_after=exc.retry_after)
    if code == "NOT_LOGIN":
        return StoreError("店铺会话已失效，请重新登录", code="NOT_LOGIN", status=401)
    if code == "ARGS":
        return StoreError(message, code="ARGS", status=400)
    if code in ("FORBIDDEN", "NOT_CONFIGURED"):
        return StoreError("店铺管理服务未正确配置，请联系管理员", code=code, status=503)
    if code in ("BRIDGE_UNAVAILABLE", "BRIDGE_TIMEOUT"):
        return StoreError("店铺管理服务暂不可用，请稍后重试", code=code, status=503)
    if "40002001" in message or "账号密码不匹配" in message:
        return StoreError("Temu 账号或密码不正确", code="BAD_PASSWORD", status=401)
    # ⚠️ 必须精确匹配 40002：用 `"40002" in message` 会命中 40002012（邮箱格式不对）之类的错误码
    if code == "40002" or re.search(r"(?<!\d)40002(?!\d)", message):
        return StoreError("Temu 账号异常，无法登录", code="ACCOUNT_ABNORMAL", status=502)
    # 登录名格式问题：手机号被当成邮箱提交时会报这个（bridge 已按 loginName 自动判断 contactType）
    if re.search(r"(?<!\d)40002012(?!\d)", message) or "邮箱格式" in message:
        return StoreError(
            "Temu 登录名格式不对：手机号请填 11 位号码（不要带 +86 或空格），邮箱请填完整地址",
            code="LOGIN_FORMAT",
            status=400,
        )
    if code == "KJ_LOGIN":
        return StoreError("Temu 账号登录失败：" + message, code="KJ_LOGIN", status=401)
    # 兜底：登录之外的调用（商品、客服）也会走这里，不要挂"登录失败"前缀
    return StoreError(message or "店铺服务返回异常", code=code or "BRIDGE_FAILED", status=502)


def _record_failure(user_id: int, account: str, exc: BridgeError, ip: str) -> None:
    code = str(exc.code or "ERR")
    cooldown_until = None
    status = "logged_out"
    if code in ("54001", "CAPTCHA"):
        cooldown_until = now_utc() + CAPTCHA_COOLDOWN
        status = "cooldown"
    elif code == "RATE_LIMIT" and exc.retry_after:
        cooldown_until = now_utc() + timedelta(seconds=int(exc.retry_after))
        status = "cooldown"
    upsert_temu_session(
        user_id,
        account=account,
        account_hash=account_hash(account),
        account_masked=mask_account(account),
        status=status,
        cooldown_until=cooldown_until,
        last_error_code=code[:40],
        last_error=(exc.message or "")[:255],
    )
    add_temu_login_log(user_id, mask_account(account), "login_failed", code, exc.message, ip)


def login(
    user: dict,
    *,
    account: str = "",
    password: str = "",
    mall_id: Optional[str] = None,
    remember: bool = False,
    use_remembered: bool = False,
    account_id: Optional[int] = None,
    verify_code: Optional[str] = None,
    ip: str = "",
) -> dict:
    """登录店铺。

    - `use_remembered=True`：用「记住密码」里的凭据；
    - `account_id`：用「店铺账号库」里指定那条的凭据（切换店铺走这里）；
    - 两者都不传则用前端提交的账号密码。
    """
    user_id = user["id"]

    if not temu_bridge.configured():
        raise StoreError("店铺管理服务未配置，请联系管理员", code="NOT_CONFIGURED", status=503)

    if account_id is not None:
        saved = store_accounts.credential_for(user_id, account_id)
        if not saved:
            raise StoreError("该店铺没有可用的已保存密码，请重新输入", code="NO_SAVED_PASSWORD", status=400)
        account = saved["account"]
        password = saved["password"]
        remember = True
    elif use_remembered:
        saved = remembered_credentials(user_id)
        if not saved:
            raise StoreError("没有可用的已保存密码，请输入账号密码", code="NO_REMEMBERED", status=400)
        account = account or saved["account"]
        password = saved["password"]
        remember = True

    account = (account or "").strip()
    if len(account) < 3:
        raise StoreError("请输入 Temu 账号", code="ARGS", status=400)
    if not password:
        raise StoreError("请输入 Temu 密码", code="ARGS", status=400)

    current = get_temu_session(user_id)
    if current:
        # 输入的是另一个账号 → 自动释放旧会话，不让用户先手动「退出」
        # （前端显示「已失效」而后端 status 仍是 active 的情况下，用户会被卡死在 409）
        same_account = bool(current.get("account")) and (
            str(current["account"]).strip().lower() == account.lower()
        )
        if current["status"] == "active":
            # 库里写着 active 不代表真的活着：bridge 重启/会话被顶号后，
            # 内存里的 ops 已经没了，但数据库状态还没同步。
            # 直接问 bridge（本地检查，零网络请求）再决定是拦还是放行。
            bridge_alive = False
            prev_account = str(current.get("account") or "")
            if prev_account:
                try:
                    info = temu_bridge.health(prev_account) or {}
                    bridge_alive = bool(info.get("alive")) if isinstance(info, dict) else False
                except Exception:  # noqa: BLE001 - bridge 不可达时按「已掉线」处理
                    bridge_alive = False

            if bridge_alive and same_account:
                raise StoreError(
                    "当前店铺在线，如需重新登录请先退出",
                    code="ALREADY_LOGGED_IN",
                    status=409,
                )
            # 掉线 / 换账号 / bridge 不可达 → 释放后重建
            try:
                temu_bridge.logout(current["account"])
            except Exception:  # noqa: BLE001 - 释放失败也不能拦住登录
                logger.warning("释放旧店铺会话失败，继续登录", exc_info=True)
            clear_temu_session(user_id, status="logged_out")
            current = None

    if current:
        # 冷却只针对同一个账号（换账号不该被上个账号的风控拖住）
        left = cooldown_seconds(current["cooldown_until"])
        if left > 0:
            raise StoreError(
                f"需要图形验证码，请在 {max(1, left // 60)} 分钟后重试",
                code="COOLDOWN",
                status=429,
                retry_after=left,
            )

    key = account_hash(account)
    occupied = find_temu_session_by_account(key, exclude_user_id=user_id)
    if occupied:
        owners = {item["id"]: item for item in list_users()}
        owner = owners.get(occupied["user_id"]) or {}
        label = owner.get("display_name") or owner.get("username") or f"用户 #{occupied['user_id']}"
        raise StoreError(f"该账号已由「{label}」登录中，请先让其退出", code="ACCOUNT_IN_USE", status=409)

    upsert_temu_session(
        user_id,
        account=account,
        account_hash=key,
        account_masked=mask_account(account),
        status="logging_in",
        remember=bool(remember),
        cooldown_until=None,
        last_error="",
        last_error_code="",
    )
    add_temu_login_log(user_id, mask_account(account), "login_start", "", "开始登录", ip)

    try:
        data = temu_bridge.login(account, password, mall_id, verify_code=verify_code)
    except BridgeError as exc:
        _record_failure(user_id, account, exc, ip)
        raise translate_bridge_error(exc) from None

    logged_in_at = now_utc()
    upsert_temu_session(
        user_id,
        account=account,
        account_hash=key,
        account_masked=mask_account(account),
        mall_id=str(data.get("mallId") or "") or None,
        mall_name=str(data.get("mallName") or ""),
        mall_list=data.get("mallList") or [],
        status="active",
        login_at=logged_in_at,
        last_seen_at=logged_in_at,
        expires_at=logged_in_at + timedelta(hours=STORE_SESSION_HOURS),
        cooldown_until=None,
        last_error="",
        last_error_code="",
        remember=bool(remember),
    )

    if remember and credential_cipher.enabled():
        encrypted = credential_cipher.encrypt(password)
        save_temu_credential(user_id, account, encrypted)
        # 同步进「店铺账号库」：同一账号重复登录就更新密文，便于后续切换
        store_accounts.save(
            user_id,
            account=account,
            password_cipher=encrypted,
            mall_name=data.get("mallName") or "",
        )
    else:
        # 未勾选（或服务端没配密钥）：不保留任何密码残留
        delete_temu_credential(user_id)

    add_temu_login_log(user_id, mask_account(account), "login", "1000000", f"登录成功：{data.get('mallName') or ''}", ip)
    return view(user_id)


def logout(user: dict, *, ip: str = "") -> dict:
    user_id = user["id"]
    row = get_temu_session(user_id)
    if not row or row["status"] == "logged_out" or not row["account"]:
        if row:
            clear_temu_session(user_id)
        add_temu_login_log(user_id, (row or {}).get("account_masked", ""), "logout", "", "无活跃会话", ip)
        return view(user_id)

    note = "已退出登录"
    if temu_bridge.configured():
        try:
            temu_bridge.logout(row["account"])
        except BridgeError as exc:
            note = f"已退出本地会话（店铺服务返回：{exc.message}）"

    clear_temu_session(user_id)
    add_temu_login_log(user_id, row["account_masked"], "logout", "", note, ip)
    return view(user_id)


def revoke(user_id: int, *, reason: str = "access_revoked", ip: str = "") -> bool:
    """强制登出（权限被收回 / 管理员操作）：销毁 bridge 会话并删除已保存凭据。"""
    row = get_temu_session(user_id)
    if not row:
        delete_temu_credential(user_id)
        return False
    if row["account"] and temu_bridge.configured():
        try:
            temu_bridge.logout(row["account"])
        except BridgeError:
            pass
    clear_temu_session(user_id)
    delete_temu_credential(user_id)
    add_temu_login_log(user_id, row["account_masked"], reason, "", "已强制退出店铺会话", ip)
    return True


def refresh(user: dict, *, ip: str = "") -> dict:
    """探活：失效时把状态置为 invalid（不自动重登）。"""
    user_id = user["id"]
    row = get_temu_session(user_id)
    if not row or row["status"] != "active" or not row["account"]:
        return view(user_id)
    if not temu_bridge.configured():
        return view(user_id)

    try:
        health = temu_bridge.health(row["account"])
    except BridgeError as exc:
        if exc.code in ("NOT_LOGIN", "BRIDGE_UNAVAILABLE", "BRIDGE_TIMEOUT"):
            upsert_temu_session(user_id, status="invalid", last_error_code=str(exc.code), last_error=exc.message[:255])
            add_temu_login_log(user_id, row["account_masked"], "session_invalid", exc.code, exc.message, ip)
        return view(user_id)

    if not health.get("alive"):
        upsert_temu_session(user_id, status="invalid", last_error_code="NOT_LOGIN", last_error="店铺会话已失效")
        add_temu_login_log(user_id, row["account_masked"], "session_invalid", "NOT_LOGIN", "店铺会话已失效", ip)
        return view(user_id)

    touched = now_utc()
    upsert_temu_session(
        user_id,
        status="active",
        mall_name=str(health.get("mallName") or row["mall_name"]),
        mall_list=health.get("mallList") or row["mall_list"],
        last_seen_at=touched,
        expires_at=touched + timedelta(hours=STORE_SESSION_HOURS),
        last_error="",
        last_error_code="",
    )
    return view(user_id)
