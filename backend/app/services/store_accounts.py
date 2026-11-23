"""用户保存的店铺账号库（可多个，但同一时刻只登录其中一个）。

与 `store_session` 的分工：
  - store_session：当前正在操作的**唯一**活跃会话（商品查询 / 客服都按它取数）；
  - store_accounts：记住的**多个**账号密码清单，切换店铺时从这里取凭据。
"""
from __future__ import annotations

import logging
from typing import Optional

from app.database import (
    delete_store_account,
    get_store_account,
    list_store_accounts,
    save_store_account,
    touch_store_account,
)
from app.services import credential_cipher


def _helpers():
    """延迟导入：store_session 会 import 本模块，顶层互相导入会循环。"""
    from app.services.store_session import StoreError, account_hash, mask_account

    return StoreError, account_hash, mask_account

logger = logging.getLogger("temubox.store_accounts")

MAX_ACCOUNTS = 20


def _public(item: dict) -> dict:
    """对外视图：绝不返回密文。"""
    return {
        "id": item["id"],
        "account": item["account"],
        "account_masked": item["account_masked"],
        "label": item["label"] or item["mall_name"] or item["account_masked"],
        "mall_name": item["mall_name"],
        "has_password": bool(item.get("ciphertext")),
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
        "last_used_at": item["last_used_at"],
    }


def list_for_user(user_id: int, *, with_cipher: bool = False, with_password: bool = False) -> list[dict]:
    """列出用户的店铺账号库。

    - `with_cipher=True`：带密文（仅内部用）；
    - `with_password=True`：解密成明文密码（仅管理员视图使用，调用方负责写审计）。
    """
    items = []
    for row in list_store_accounts(user_id):
        detail = get_store_account(row["id"], user_id) or row
        if with_cipher:
            items.append(detail)
            continue
        item = _public(detail)
        if with_password:
            cipher = detail.get("ciphertext") or ""
            if cipher:
                try:
                    item["password"] = credential_cipher.decrypt(cipher)
                except credential_cipher.CredentialError:
                    item["password"] = ""
                    item["password_error"] = "解密失败（CREDENTIAL_KEY 可能已更换）"
            else:
                item["password"] = ""
        items.append(item)
    return items


def save(
    user_id: int,
    *,
    account: str,
    password_cipher: str,
    label: str = "",
    mall_name: str = "",
) -> dict:
    """新增/更新一个店铺账号。password_cipher 必须是已加密的密文。"""
    StoreError, account_hash, mask_account = _helpers()
    account = (account or "").strip()
    if not account:
        raise StoreError("账号不能为空", code="BAD_ACCOUNT", status=400)
    if not credential_cipher.enabled():
        raise StoreError("服务端未配置 CREDENTIAL_KEY，无法保存密码", code="NO_KEY", status=409)
    existing = list_store_accounts(user_id)
    hashed = account_hash(account)
    if not any(item["account_hash"] == hashed for item in existing) and len(existing) >= MAX_ACCOUNTS:
        raise StoreError(f"最多保存 {MAX_ACCOUNTS} 个店铺账号", code="TOO_MANY", status=409)
    row = save_store_account(
        user_id,
        account=account,
        account_hash=hashed,
        account_masked=mask_account(account),
        ciphertext=password_cipher,
        label=label,
        mall_name=mall_name,
    )
    detail = get_store_account(row["id"], user_id) or row
    return _public(detail)


def encrypt_and_save(user_id: int, *, account: str, password: str, label: str = "", mall_name: str = "") -> dict:
    StoreError, _hash, _mask = _helpers()
    if not credential_cipher.enabled():
        raise StoreError("服务端未配置 CREDENTIAL_KEY，无法保存密码", code="NO_KEY", status=409)
    return save(
        user_id,
        account=account,
        password_cipher=credential_cipher.encrypt(password),
        label=label,
        mall_name=mall_name,
    )


def credential_for(user_id: int, account_id: int) -> Optional[dict]:
    """取某个店铺的明文凭据（仅服务端内部用于切换登录）。"""
    row = get_store_account(account_id, user_id)
    if not row or not row.get("ciphertext"):
        return None
    try:
        password = credential_cipher.decrypt(row["ciphertext"])
    except credential_cipher.CredentialError:
        logger.warning("店铺账号 %s 解密失败（CREDENTIAL_KEY 可能已更换）", account_id)
        return None
    return {"account": row["account"], "password": password, "label": row["label"]}


def mark_used(account_id: int) -> None:
    touch_store_account(account_id)


def remove(user_id: int, account_id: int) -> bool:
    return delete_store_account(account_id, user_id)
