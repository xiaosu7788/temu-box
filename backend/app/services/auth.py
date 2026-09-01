from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from typing import Optional

from fastapi import HTTPException, Request

from app.config import AUTH_SECRET, SESSION_COOKIE_NAME, SESSION_MAX_AGE
from app.database import get_user, get_user_by_username

PASSWORD_ITERATIONS = 600_000
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,80}$")


def validate_username(username: str) -> str:
    username = username.strip()
    if not USERNAME_RE.fullmatch(username):
        raise ValueError("用户名需为 3-80 位字母、数字、下划线、点或短横线")
    return username


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip("=")
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${encode(salt)}${encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        decode = lambda value: base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), decode(salt_text), int(iterations))
        return hmac.compare_digest(digest, decode(digest_text))
    except (TypeError, ValueError, UnicodeError):
        return False


def _sign(value: str) -> str:
    return hmac.new(AUTH_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()


def make_session(user_id: int) -> str:
    payload = f"{user_id}.{int(time.time()) + SESSION_MAX_AGE}"
    return f"{payload}.{_sign(payload)}"


def session_user_id(token: Optional[str]) -> Optional[int]:
    if not token:
        return None
    try:
        user_text, expires_text, signature = token.split(".", 2)
        payload = f"{user_text}.{expires_text}"
        if not hmac.compare_digest(signature, _sign(payload)) or int(expires_text) < int(time.time()):
            return None
        return int(user_text)
    except (TypeError, ValueError):
        return None


def public_user(user: dict) -> dict:
    return {key: user[key] for key in ("id", "username", "display_name", "role", "status", "created_at", "approved_at")}


def current_user(request: Request) -> dict:
    user_id = session_user_id(request.cookies.get(SESSION_COOKIE_NAME))
    user = get_user(user_id) if user_id else None
    if not user or user["status"] != "approved":
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def admin_user(request: Request) -> dict:
    user = current_user(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以执行此操作")
    return user


def login_user(username: str, password: str) -> dict:
    user = get_user_by_username(username.strip())
    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if user["status"] == "pending":
        raise HTTPException(status_code=403, detail="账号正在等待管理员审核")
    if user["status"] != "approved":
        raise HTTPException(status_code=403, detail="账号未通过审核")
    return user
