from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SIDECAR_PATH = Path(__file__).resolve().parents[2] / "docker" / "updater" / "server.py"


@pytest.fixture(scope="module")
def sidecar():
    spec = importlib.util.spec_from_file_location("temubox_updater_sidecar", SIDECAR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_compose_ps_accepts_array_and_line_formats(sidecar):
    items = [{"Service": "backend", "State": "running"}, {"Service": "frontend", "State": "running"}]

    assert [row["Service"] for row in sidecar.parse_compose_ps(json.dumps(items))] == ["backend", "frontend"]
    lines = "\n".join(json.dumps(item) for item in items) + "\n"
    assert [row["Service"] for row in sidecar.parse_compose_ps(lines)] == ["backend", "frontend"]
    # 旧版 compose 混着告警行时只挑出合法 JSON
    assert sidecar.parse_compose_ps('not json\n{"Service": "backend"}') == [{"Service": "backend"}]
    assert sidecar.parse_compose_ps("") == []


def test_compose_scope_never_maintains_the_updater_itself(sidecar, tmp_path, monkeypatch):
    monkeypatch.setattr(sidecar, "PROJECT_DIR", tmp_path)
    env_file = tmp_path / sidecar.ENV_FILE

    env_file.write_text("POSTGRES_PASSWORD=x\n", encoding="utf-8")
    profiles, services = sidecar.compose_scope()
    assert profiles == ("internal-db",)
    assert services == ["db", "backend", "frontend"]
    assert "updater" not in services

    # 配了 DATABASE_URL（外部数据库）就不再带 internal-db，也不维护 db
    env_file.write_text("POSTGRES_PASSWORD=x\nDATABASE_URL=postgresql+psycopg://u:p@h:5432/db\n", encoding="utf-8")
    profiles, services = sidecar.compose_scope()
    assert profiles == ()
    assert services == ["backend", "frontend"]


def test_readiness_reports_blocking_prerequisites(sidecar, tmp_path, monkeypatch):
    monkeypatch.setattr(sidecar, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(sidecar, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(sidecar, "TOKEN", "secret")

    tools = {"docker": "28.0.0", "compose": "2.30.0", "git": "2.45.0"}
    notes = sidecar.readiness(tools, True)
    assert any("docker-compose.yml" in note for note in notes)
    assert any("不是 git 仓库" in note for note in notes)
    assert any(sidecar.ENV_FILE in note for note in notes)

    # 目录补齐后只剩「没有备份脚本」这一条提示，可执行
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / sidecar.ENV_FILE).write_text("", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    assert sidecar.readiness(tools, True) == ["缺少 scripts/backup.sh：更新前的数据备份会被跳过"]

    (tmp_path / "scripts" / "backup.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    assert sidecar.readiness(tools, True) == []

    monkeypatch.setattr(sidecar, "TOKEN", "")
    blocking = sidecar.readiness({}, False)
    assert any("UPDATER_TOKEN" in note for note in blocking)
    assert any("docker CLI" in note for note in blocking)
    assert any("compose" in note for note in blocking)
    assert any("git" in note for note in blocking)
    assert any("状态目录不可写" in note for note in blocking)


def test_job_snapshot_serves_incremental_lines_and_step_states(sidecar):
    job = sidecar.Job("update")
    assert job.snapshot(0)["title"] == sidecar.KIND_TITLES["update"]
    assert job.snapshot(0)["total"] == len(sidecar.KIND_STEPS["update"])

    job.begin(1)
    job.add("拉取代码")
    snapshot = job.snapshot(0)
    assert snapshot["status"] == "running"
    assert snapshot["current"] == 1
    assert [step["state"] for step in snapshot["steps"]][:2] == ["running", "pending"]
    assert snapshot["lines"][-1].endswith("拉取代码")
    read = snapshot["line_count"]

    job.add("拉取镜像")
    tail = job.snapshot(read)
    assert tail["line_count"] == read + 1
    assert len(tail["lines"]) == 1
    assert tail["lines"][0].endswith("拉取镜像")

    job.fail("镜像拉取失败", step="3/5")
    failed = job.snapshot(0)
    assert failed["status"] == "failed"
    assert failed["detail"] == {"step": "3/5"}
    # current 指向失败的那一步：第 1 步开始后就失败，前面没有已完成步骤
    assert [step["state"] for step in failed["steps"]][:2] == ["failed", "pending"]
    assert "结果：failed" in failed["lines"][-1]

    later = sidecar.Job("update")
    later.begin(2)
    later.fail("镜像拉取失败")
    assert [step["state"] for step in later.snapshot(0)["steps"]][:3] == ["done", "failed", "pending"]

    job.finish("ok", "更新完成", before="aaa", after="bbb")
    done = job.snapshot(0)
    assert done["status"] == "ok"
    assert done["message"] == "更新完成"
    assert {step["state"] for step in done["steps"]} == {"done"}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _request(url: str, token: str = "", body: bytes | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET")
    if token:
        request.add_header("X-Updater-Token", token)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def test_sidecar_enforces_token_and_rejects_request_parameters(tmp_path):
    """真正启动一遍边车脚本：验证无令牌拒绝、带令牌放行、POST 不接受任何参数。"""
    project = tmp_path / "temu-box"
    project.mkdir()
    (project / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (project / ".env.docker").write_text("POSTGRES_PASSWORD=x\n", encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    port = _free_port()

    env = dict(os.environ)
    env.update({
        "PROJECT_DIR": str(project),
        "UPDATER_STATE_DIR": str(state),
        "UPDATER_TOKEN": "unit-test-token",
        "UPDATER_HOST": "127.0.0.1",
        "UPDATER_PORT": str(port),
    })
    process = subprocess.Popen(
        [sys.executable, str(SIDECAR_PATH)],
        env=env,
        cwd=str(project),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 30
        while True:
            if process.poll() is not None:
                output = (process.stdout.read() or b"").decode("utf-8", "replace")
                pytest.fail(f"边车启动即退出（{process.returncode}）：{output}")
            try:
                code, payload = _request(f"{base}/health")
            except OSError:
                code, payload = 0, {}
            if code == 200:
                break
            if time.time() > deadline:
                pytest.fail("边车未在 30 秒内就绪")
            time.sleep(0.3)
        assert payload["status"] == "ok"
        assert payload["configured"] is True

        # 未带令牌 / 令牌错误都拒绝
        assert _request(f"{base}/status")[0] == 401
        assert _request(f"{base}/status?token=wrong")[0] == 401

        # 带正确令牌：/job 不需要 docker（无任务时回空）
        code, payload = _request(f"{base}/job?since=3", token="unit-test-token")
        assert code == 200
        assert payload["job"] is None
        assert payload["busy"] is False
        assert isinstance(payload["last_result"], dict)

        # POST 只接受空体或 {}：带参数一律 400
        assert _request(f"{base}/update", token="unit-test-token", body=b'{"command": "sh"}')[0] == 400
        assert _request(f"{base}/update", token="unit-test-token", body=b"")[0] == 202
        assert _request(f"{base}/remove", token="unit-test-token", body=b"{}")[0] == 404
        assert _request(f"{base}/update", token="unit-test-token", body=b"x" * 5000)[0] == 413
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
