from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from app.database import create_user
from app.main import app
from app.services import updater
from app.services.auth import hash_password


class _StubSidecar:
    """本地起一个 HTTP 服务替身运维边车，记录收到的每个请求。"""

    def __init__(self, responses: dict | None = None) -> None:
        self.seen: list = []
        self.responses = responses or {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode("utf-8") if length else ""
                outer.seen.append({
                    "method": self.command,
                    "path": self.path,
                    "token": self.headers.get("X-Updater-Token") or "",
                    "content_type": self.headers.get("Content-Type") or "",
                    "body": body,
                })
                payload, status = outer.responses.get((self.command, self.path.split("?")[0]), ({"ok": True, "job": None}, 200))
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args) -> None:  # 请求日志会淹没测试输出
                pass

            do_GET = _reply
            do_POST = _reply

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def sidecar(monkeypatch):
    stub = _StubSidecar()
    monkeypatch.setattr(updater, "UPDATER_URL", stub.url)
    monkeypatch.setattr(updater, "UPDATER_TOKEN", "test-token")
    yield stub
    stub.close()


def test_proxy_calls_fixed_actions_with_token_and_empty_body(sidecar):
    updater.status()
    updater.job(7)
    updater.start("update")

    assert [(item["method"], item["path"]) for item in sidecar.seen] == [
        ("GET", "/status"),
        ("GET", "/job?since=7"),
        ("POST", "/update"),
    ]
    assert {item["token"] for item in sidecar.seen} == {"test-token"}
    posts = [item for item in sidecar.seen if item["method"] == "POST"]
    # 边车只接受空体或 {}，后端永远发空对象，不带任何参数
    assert [item["body"] for item in posts] == ["{}"]
    assert posts[0]["content_type"] == "application/json"


def test_proxy_passes_through_sidecar_payload(monkeypatch):
    stub = _StubSidecar({
        ("GET", "/status"): ({"ok": True, "data": {"project": {"branch": "main"}}, "job": {"id": "u-1"}}, 200),
        ("GET", "/job"): ({"ok": True, "job": {"id": "u-1", "status": "running", "lines": ["a"]}, "last_result": {"status": "ok"}, "busy": True}, 200),
        ("POST", "/rollback"): ({"ok": True, "job": {"id": "r-2", "kind": "rollback"}}, 202),
    })
    monkeypatch.setattr(updater, "UPDATER_URL", stub.url)
    monkeypatch.setattr(updater, "UPDATER_TOKEN", "test-token")
    try:
        snapshot = updater.status()
        assert snapshot["enabled"] is True
        assert snapshot["data"] == {"project": {"branch": "main"}}
        assert snapshot["job"] == {"id": "u-1"}

        payload = updater.job(0)
        assert payload["busy"] is True
        assert payload["job"]["lines"] == ["a"]
        assert payload["last_result"] == {"status": "ok"}

        started = updater.start("rollback")
        assert started == {"enabled": True, "job": {"id": "r-2", "kind": "rollback"}, "action": "rollback"}
    finally:
        stub.close()


def test_sidecar_rejection_keeps_status_and_message(monkeypatch):
    stub = _StubSidecar({("POST", "/update"): ({"ok": False, "error": "已有任务在执行，请等待完成"}, 409)})
    monkeypatch.setattr(updater, "UPDATER_URL", stub.url)
    monkeypatch.setattr(updater, "UPDATER_TOKEN", "test-token")
    try:
        with pytest.raises(updater.UpdaterError) as excinfo:
            updater.start("update")
        assert excinfo.value.status == 409
        assert "已有任务在执行" in str(excinfo.value)
        with pytest.raises(updater.UpdaterError) as unknown:
            updater.start("rm-rf")
        assert unknown.value.status == 503
    finally:
        stub.close()


def test_update_endpoints_require_admin_and_start_job(sidecar):
    with TestClient(app) as client:
        create_user("hotupdate_admin", hash_password("hotpass123"), role="admin", status="approved")
        create_user("hotupdate_reader", hash_password("hotreader123"), role="user", status="approved")

        assert client.post("/api/auth/login", json={"username": "hotupdate_reader", "password": "hotreader123"}).status_code == 200
        assert client.get("/api/admin/update/status").status_code == 403
        assert client.get("/api/admin/update/job").status_code == 403
        assert client.post("/api/admin/update").status_code == 403
        assert client.post("/api/admin/update/rollback").status_code == 403

        assert client.post("/api/auth/login", json={"username": "hotupdate_admin", "password": "hotpass123"}).status_code == 200
        status = client.get("/api/admin/update/status")
        assert status.status_code == 200
        assert status.json()["enabled"] is True

        started = client.post("/api/admin/update")
        assert started.status_code == 202
        assert started.json()["action"] == "update"

        rolled_back = client.post("/api/admin/update/rollback")
        assert rolled_back.status_code == 202
        assert rolled_back.json()["action"] == "rollback"

        logs = client.get("/api/admin/audit-logs", params={"action": "update.update"}).json()
        assert logs["total"] >= 1


def test_update_endpoints_report_not_configured_without_token(monkeypatch):
    monkeypatch.setattr(updater, "UPDATER_TOKEN", "")
    with TestClient(app) as client:
        create_user("hotupdate_offline", hash_password("hotoffline123"), role="admin", status="approved")
        assert client.post("/api/auth/login", json={"username": "hotupdate_offline", "password": "hotoffline123"}).status_code == 200

        status = client.get("/api/admin/update/status")
        assert status.status_code == 200
        body = status.json()
        assert body["enabled"] is False
        assert body["data"] is None
        assert "UPDATER_TOKEN" in body["error"]

        blocked = client.post("/api/admin/update")
        assert blocked.status_code == 503
        assert "UPDATER_TOKEN" in blocked.json()["detail"]
        assert client.get("/api/admin/update/job").status_code == 503
