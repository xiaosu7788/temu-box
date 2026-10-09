"""一键热更新：后端到运维边车的薄代理。

边车（docker/updater/server.py）跑在 compose 内部网络里，只提供固定动作
（status / update / rollback），不发布端口、不接受参数化命令。这里只做三件事：
拼请求、把连接层错误翻译成管理员看得懂的中文、把边车返回的 JSON 原样交给路由层。
用标准库 urllib，不新增第三方依赖。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from app.config import UPDATER_ACTION_TIMEOUT, UPDATER_STATUS_TIMEOUT, UPDATER_TOKEN, UPDATER_URL

NOT_CONFIGURED = (
    "一键热更新未启用：请在服务器 .env.docker 中设置 UPDATER_TOKEN（可用 openssl rand -hex 32 生成），"
    "然后执行 ./scripts/enable-updater.sh 启动运维边车"
)

ACTIONS = {"update", "rollback"}


class UpdaterError(Exception):
    """调用边车失败。message 直接面向管理员展示；status 决定回给前端的 HTTP 状态码。"""

    def __init__(self, message: str, status: int = 503) -> None:
        super().__init__(message)
        self.status = status


def enabled() -> bool:
    return bool(UPDATER_TOKEN)


def _error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8", "replace") or "{}")
    except (ValueError, OSError):
        payload = {}
    reason = str(payload.get("error") or "").strip() if isinstance(payload, dict) else ""
    if reason:
        return reason
    return f"更新边车返回 HTTP {exc.code}"


def _request(method: str, path: str, params: dict | None = None, timeout: int = 20) -> dict:
    if not enabled():
        raise UpdaterError(NOT_CONFIGURED)
    url = f"{UPDATER_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    # POST 固定发空对象：边车只接受空体或 {}，任何参数都会被认为是越界
    data = b"{}" if method == "POST" else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("X-Updater-Token", UPDATER_TOKEN)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise UpdaterError(_error_detail(exc), status=exc.code) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise UpdaterError(
            f"无法连接运维边车（{UPDATER_URL}）：{reason}。"
            "请确认已在服务器执行 docker compose --env-file .env.docker --profile ops up -d updater"
        ) from exc
    except (TimeoutError, OSError) as exc:
        raise UpdaterError(f"运维边车无响应（{UPDATER_URL}）：{exc}") from exc
    try:
        payload = json.loads(body or "{}")
    except ValueError as exc:
        raise UpdaterError("运维边车返回了无法解析的响应") from exc
    if not isinstance(payload, dict):
        raise UpdaterError("运维边车返回了无法解析的响应")
    if payload.get("ok") is False:
        raise UpdaterError(str(payload.get("error") or "运维边车拒绝了请求"))
    return payload


def status() -> dict:
    """边车状态快照 + 当前任务（未配置时只回 enabled=False，不报错）。"""
    if not enabled():
        return {"enabled": False, "url": UPDATER_URL, "error": NOT_CONFIGURED, "data": None, "job": None}
    payload = _request("GET", "/status", timeout=UPDATER_STATUS_TIMEOUT)
    return {
        "enabled": True,
        "url": UPDATER_URL,
        "error": "",
        "data": payload.get("data") or {},
        "job": payload.get("job"),
    }


def job(since: int = 0) -> dict:
    payload = _request("GET", "/job", params={"since": max(0, int(since))}, timeout=UPDATER_STATUS_TIMEOUT)
    return {
        "enabled": True,
        "job": payload.get("job"),
        "last_result": payload.get("last_result") or {},
        "busy": bool(payload.get("busy")),
    }


def start(action: str) -> dict:
    if action not in ACTIONS:
        raise UpdaterError(f"不支持的操作：{action}")
    payload = _request("POST", f"/{action}", timeout=UPDATER_ACTION_TIMEOUT)
    return {"enabled": True, "job": payload.get("job"), "action": action}
