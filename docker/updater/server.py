#!/usr/bin/env python3
"""Temu-Box 运维边车：把「拉代码 → 拉镜像 → 重启 → 健康检查」搬进 Web 后台。

设计约束（有意为之，改之前先读）：
* 只提供固定动作（status / update / rollback），不接受任何命令或参数化命令，
  避免把 docker.sock 暴露成一个远程 shell；
* 不发布端口，只挂在 compose 内部网络上，由 backend 以管理员身份代理调用；
* 更新时只操作 backend / frontend 两个服务，永不重建自己（否则更新过程会掐断自己）；
* 项目目录挂载到容器内的**同一个绝对路径**，这样 compose 文件里的相对路径
  （./data 等）在守护进程侧解析结果与宿主机一致。
"""
from __future__ import annotations

import hmac
import json
import os
import shlex
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PROJECT_DIR = Path(os.environ.get("PROJECT_DIR", "/var/www/temu-box")).expanduser()
BRANCH = os.environ.get("UPDATER_BRANCH", "main").strip() or "main"
STATE_DIR = Path(os.environ.get("UPDATER_STATE_DIR", "/var/lib/temubox-updater"))
TOKEN = os.environ.get("UPDATER_TOKEN", "").strip()
HOST = os.environ.get("UPDATER_HOST", "0.0.0.0")
PORT = int(os.environ.get("UPDATER_PORT", "8899"))

ENV_FILE = ".env.docker"
PROJECT_SERVICES = ("backend", "frontend")
PREVIOUS_TAG = "previous"
REMOTE_CACHE_SECONDS = 30
HEALTH_TIMEOUT = 180
MAX_BODY_BYTES = 4096

_remote_cache: dict = {"at": 0.0, "sha": "", "subject": "", "error": None}
_cache_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- 进程执行


def child_env() -> dict:
    env = os.environ.copy()
    # 边车自己是被 --profile ops 启动的；必须清掉，否则更新时的 up -d 会把自己也纳入
    # 启用集合，镜像一变就被重建，正在跑的更新任务会被当场掐断。
    env.pop("COMPOSE_PROFILES", None)
    return env


def capture(cmd: list[str], timeout: int = 60, cwd: Path | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd or PROJECT_DIR),
            env=child_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return 127, f"命令不存在：{cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"命令超时（{timeout}s）：{shlex.join(cmd)}"
    return proc.returncode, (proc.stdout or "").strip()


def stream(job: "Job", cmd: list[str], timeout: int = 900, cwd: Path | None = None) -> int:
    """执行命令并把输出逐行实时写进任务日志。"""
    job.add(f"$ {shlex.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd or PROJECT_DIR),
            env=child_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        job.add(f"[错误] 命令不存在：{cmd[0]}")
        return 127

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            job.add(line.rstrip("\r\n"))
        proc.stdout.close()

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        job.add(f"[错误] 命令超时（{timeout}s），已终止")
        return 124
    reader.join(timeout=5)
    return code


# --------------------------------------------------------------------------- git / compose 查询


def git(*args: str, timeout: int = 60) -> tuple[int, str]:
    return capture(["git", "-C", str(PROJECT_DIR), *args], timeout=timeout)


def git_value(*args: str, timeout: int = 60) -> str:
    code, out = git(*args, timeout=timeout)
    return out.splitlines()[0].strip() if code == 0 and out else ""


def env_file_values() -> dict:
    values: dict = {}
    path = PROJECT_DIR / ENV_FILE
    if not path.is_file():
        return values
    try:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    except OSError:
        return values
    return values


def uses_internal_db() -> bool:
    return not env_file_values().get("DATABASE_URL", "").strip()


def compose(*args: str, profiles: tuple[str, ...] = ()) -> list[str]:
    cmd = ["docker", "compose", "--env-file", ENV_FILE]
    for profile in profiles:
        cmd += ["--profile", profile]
    return cmd + list(args)


def compose_json(*args: str) -> dict:
    code, out = capture(compose(*args), timeout=120)
    if code != 0 or not out:
        return {}
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def compose_images() -> dict:
    """按当前 compose 文件与 .env.docker 解析出各服务的镜像引用。"""
    config = compose_json("config", "--format", "json")
    services = config.get("services") if isinstance(config.get("services"), dict) else {}
    images = {}
    for name, spec in services.items():
        if isinstance(spec, dict) and spec.get("image"):
            images[name] = str(spec["image"])
    if not images:
        values = env_file_values()
        images = {
            "backend": values.get("TEMUBOX_BACKEND_IMAGE", "ghcr.io/xiaosu7788/temu-box-backend:latest"),
            "frontend": values.get("TEMUBOX_FRONTEND_IMAGE", "ghcr.io/xiaosu7788/temu-box-frontend:latest"),
        }
    return images


def previous_ref(image: str) -> str:
    head, sep, tail = image.rpartition(":")
    repo = head if sep and "/" not in tail else image
    return f"{repo}:{PREVIOUS_TAG}"


def image_state(ref: str) -> dict:
    code, out = capture(["docker", "image", "inspect", ref, "--format", "{{.Id}}|{{.Created}}"], timeout=30)
    if code != 0 or "|" not in out:
        return {"ref": ref, "present": False, "id": "", "created": "", "short": ""}
    image_id, _, created = out.partition("|")
    image_id = image_id.strip()
    return {
        "ref": ref,
        "present": True,
        "id": image_id,
        "short": image_id.replace("sha256:", "")[:12],
        "created": created.strip(),
    }


def service_container_id(service: str) -> str:
    code, out = capture(compose("ps", "-q", service), timeout=60)
    return out.splitlines()[0].strip() if code == 0 and out else ""


def running_image_id(service: str) -> str:
    container = service_container_id(service)
    if not container:
        return ""
    code, out = capture(["docker", "inspect", container, "--format", "{{.Image}}"], timeout=30)
    return out.strip() if code == 0 else ""


def tool_versions() -> dict:
    versions = {}
    for key, cmd in (("docker", ["docker", "version", "--format", "{{.Client.Version}}"]),
                     ("compose", ["docker", "compose", "version", "--short"]),
                     ("git", ["git", "--version"])):
        code, out = capture(cmd, timeout=30)
        versions[key] = out.strip() if code == 0 else ""
    return versions


# --------------------------------------------------------------------------- 任务与状态

VERSION = "1.0.0"
MAX_LOG_LINES = 1500

RESULT_FILE = "last_result.json"
ROLLBACK_FILE = "rollback.json"

KIND_TITLES = {"update": "更新到最新版本", "rollback": "回滚到上一个版本"}
KIND_STEPS = {
    "update": ("检查版本", "备份数据", "拉取代码", "拉取镜像", "重启服务", "健康检查"),
    "rollback": ("读取回滚点", "恢复代码", "恢复镜像", "重启服务", "健康检查"),
}


class Job:
    """一次更新/回滚的执行记录。日志按行保留，前端按 since 增量拉取。"""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.title = KIND_TITLES.get(kind, kind)
        self.steps = KIND_STEPS.get(kind, ())
        self.id = f"{kind}-{int(time.time() * 1000):x}"
        self.status = "running"  # running / ok / failed
        self.started_at = now_iso()
        self.finished_at = ""
        self.message = ""
        self.detail: dict = {}
        self.current = 0  # 正在执行的步骤序号（从 1 开始，0 表示还没进入步骤）
        self._lines: list[str] = []
        self._lock = threading.Lock()

    def add(self, message: str = "") -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self._lines.append(f"[{stamp}] {message}" if message else "")
            del self._lines[:-MAX_LOG_LINES]

    def begin(self, index: int) -> None:
        """标记第 index 个步骤（从 1 开始）开始执行。"""
        self.current = index
        self.add()
        self.add(f"== {index}/{len(self.steps)} {self.steps[index - 1]} ==")

    def fail(self, message: str, **detail) -> None:
        self.finish("failed", message, **detail)

    def finish(self, status: str, message: str, **detail) -> None:
        self.status = status
        self.message = message
        self.detail = detail
        self.finished_at = now_iso()
        for label, value in detail.items():
            self.add(f"{label}: {value}")
        self.add(f"结果：{status} —— {message}")

    def snapshot(self, since: int = 0) -> dict:
        with self._lock:
            total = len(self._lines)
            lines = self._lines[max(0, min(since, total)):]
        steps = []
        for number, label in enumerate(self.steps, start=1):
            if self.status == "running":
                state = "done" if number < self.current else ("running" if number == self.current else "pending")
            elif self.status == "ok":
                state = "done"
            else:
                state = "done" if number < self.current else ("failed" if number == self.current else "pending")
            steps.append({"label": label, "state": state})
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "message": self.message,
            "detail": self.detail,
            "current": self.current,
            "total": len(self.steps),
            "steps": steps,
            "lines": lines,
            "line_count": total,
        }


def state_path(name: str) -> Path:
    return STATE_DIR / name


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, data: dict) -> None:
    # 原子写入：日志/状态目录在命名卷里，进程被重建时不能留下半个 json
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def last_result() -> dict:
    return read_json(state_path(RESULT_FILE))


def save_result(job: Job, **extra) -> None:
    write_json(state_path(RESULT_FILE), {
        "kind": job.kind,
        "status": job.status,
        "message": job.message,
        "at": job.finished_at or now_iso(),
        **job.detail,
        **extra,
    })


def rollback_point() -> dict:
    return read_json(state_path(ROLLBACK_FILE))


def save_rollback_point(data: dict) -> None:
    write_json(state_path(ROLLBACK_FILE), data)


def clear_rollback_point() -> None:
    try:
        state_path(ROLLBACK_FILE).unlink()
    except OSError:
        pass


_job_lock = threading.Lock()
_current: Job | None = None


def current_job() -> Job | None:
    with _job_lock:
        return _current


def start_job(kind: str) -> tuple[Job | None, str]:
    """串行启动任务：同一时刻只允许一个更新/回滚在跑，避免并发改镜像。"""
    global _current
    with _job_lock:
        if _current is not None and _current.status == "running":
            return None, f"已有任务在执行：{_current.title}（{_current.id}）"
        job = Job(kind)
        _current = job
    threading.Thread(target=_run_job, args=(job,), daemon=True, name=f"updater-{job.id}").start()
    return job, ""


def _run_job(job: Job) -> None:
    action = {"update": do_update, "rollback": do_rollback}.get(job.kind)
    if action is None:
        job.fail(f"未知的操作类型：{job.kind}")
        save_result(job)
        return
    try:
        action(job)
    except Exception as exc:  # 未预期异常也必须落到任务状态里，不能让线程静默死掉
        job.add(traceback.format_exc())
        job.fail(f"执行异常：{exc}")
    finally:
        save_result(job)


# --------------------------------------------------------------------------- 状态采集


def compose_scope() -> tuple[tuple[str, ...], list[str]]:
    """返回 (profile 参数, 需要维护的服务)。

    与 scripts/docker-update-pull.sh 保持一致：使用外部 PostgreSQL 时不启用 internal-db；
    使用内置数据库时把 db 一并纳入维护范围。updater 自己永远不在这个列表里。
    """
    if uses_internal_db():
        return ("internal-db",), ["db", *PROJECT_SERVICES]
    return (), list(PROJECT_SERVICES)


def commit_info(rev: str = "HEAD") -> dict:
    code, out = git("log", "-1", "--pretty=%H\x1f%h\x1f%s\x1f%cI", rev, timeout=30)
    if code != 0 or "\x1f" not in out:
        return {"sha": "", "short": "", "subject": "", "date": ""}
    parts = (out.split("\x1f") + ["", "", "", ""])[:4]
    return {"sha": parts[0], "short": parts[1], "subject": parts[2], "date": parts[3]}


def working_tree_dirty() -> bool:
    code, out = git("status", "--porcelain", "--untracked-files=no", timeout=30)
    return code == 0 and bool(out.strip())


def fetch_remote() -> dict:
    """拉取远端分支信息（更新 FETCH_HEAD 与远端引用，不改工作区）。"""
    checked_at = time.time()
    code, out = git("fetch", "--quiet", "--prune", "origin", BRANCH, timeout=180)
    if code != 0:
        return {"at": checked_at, "sha": "", "subject": "", "error": (out.splitlines() or ["git fetch 失败"])[-1]}
    sha = git_value("rev-parse", "FETCH_HEAD")
    if not sha:
        return {"at": checked_at, "sha": "", "subject": "", "error": "无法解析远端提交"}
    info = commit_info(sha)
    return {"at": checked_at, "sha": sha, "subject": info.get("subject", ""), "date": info.get("date", ""), "error": None}


def remote_state(busy: bool) -> dict:
    """远端提交信息，30 秒内复用；任务执行期间不 fetch，避免和更新流程抢仓库。"""
    with _cache_lock:
        cached = dict(_remote_cache)
    if busy:
        return cached or {"at": 0.0, "sha": "", "subject": "", "error": None}
    if time.time() - float(cached.get("at") or 0) < REMOTE_CACHE_SECONDS:
        return cached
    result = fetch_remote()
    with _cache_lock:
        _remote_cache.update(result)
    return result


def parse_compose_ps(out: str) -> list[dict]:
    """兼容两种输出：新版 Compose 是 JSON 数组，旧版是一行一个 JSON 对象。"""
    text = out.strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    items = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            items.append(data)
    return items


def inspect_container(container: str) -> dict:
    if not container:
        return {}
    code, out = capture([
        "docker", "inspect", container, "--format",
        "{{.Image}}|{{.State.StartedAt}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}",
    ], timeout=30)
    if code != 0 or "|" not in out:
        return {}
    image_id, _, rest = out.partition("|")
    started_at, _, health = rest.partition("|")
    return {"image_id": image_id.strip(), "started_at": started_at.strip(), "health": health.strip()}


def service_states() -> list[dict]:
    profiles, services = compose_scope()
    code, out = capture(compose("ps", "--format", "json", *services, profiles=profiles), timeout=90)
    listed = {str(item.get("Service") or ""): item for item in parse_compose_ps(out)} if code == 0 else {}
    states = []
    for name in services:
        item = listed.get(name) or {}
        inspect = inspect_container(str(item.get("Name") or "") or service_container_id(name))
        states.append({
            "name": name,
            "state": str(item.get("State") or "absent"),
            "status": str(item.get("Status") or "未运行"),
            "health": str(item.get("Health") or inspect.get("health") or ""),
            "image": str(item.get("Image") or ""),
            "image_id": inspect.get("image_id", ""),
            "started_at": inspect.get("started_at", ""),
        })
    return states


def state_dir_usable() -> tuple[bool, bool]:
    """返回 (可写, 是否为挂载卷)。不可写时回滚点存不下来，必须当成硬阻塞。"""
    probe = state_path(".probe")
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        writable = True
    except OSError:
        writable = False
    try:
        mounted = os.path.ismount(STATE_DIR)
    except OSError:
        mounted = False
    return writable, mounted


def readiness(tools: dict, state_ok: bool) -> list[str]:
    """列出会挡住更新的前置条件；只剩提示（非阻塞）时也算可执行。"""
    notes = []
    if not TOKEN:
        notes.append("未配置 UPDATER_TOKEN：边车会拒绝所有操作")
    if not PROJECT_DIR.is_dir():
        notes.append(f"项目目录不存在：{PROJECT_DIR}（检查 compose 里的挂载路径）")
    else:
        if not (PROJECT_DIR / "docker-compose.yml").is_file():
            notes.append(f"项目目录没有 docker-compose.yml：{PROJECT_DIR}")
        if not (PROJECT_DIR / ".git").exists():
            notes.append("项目目录不是 git 仓库，无法拉取代码")
        if not (PROJECT_DIR / "scripts" / "backup.sh").is_file():
            notes.append("缺少 scripts/backup.sh：更新前的数据备份会被跳过")
    if not (PROJECT_DIR / ENV_FILE).is_file():
        notes.append(f"缺少 {ENV_FILE}：docker compose 无法解析配置")
    if not tools.get("docker"):
        notes.append("容器内没有可用的 docker CLI")
    if not tools.get("compose"):
        notes.append("容器内没有 docker compose 插件")
    if not tools.get("git"):
        notes.append("容器内没有 git")
    if not state_ok:
        notes.append(f"状态目录不可写：{STATE_DIR}（回滚点无法保存）")
    return notes


def status_snapshot() -> dict:
    job = current_job()
    busy = job is not None and job.status == "running"
    tools = tool_versions()
    state_ok, state_mounted = state_dir_usable()
    images = compose_images()
    remote = remote_state(busy)
    local = commit_info("HEAD")
    remote_sha = str(remote.get("sha") or "")
    dirty = working_tree_dirty()

    behind = 0
    fast_forward = False
    incoming: list[dict] = []
    if remote_sha and remote_sha != local["sha"]:
        code, out = git("rev-list", "--count", f"HEAD..{remote_sha}", timeout=30)
        behind = int(out.strip()) if code == 0 and out.strip().isdigit() else 0
        fast_forward = git("merge-base", "--is-ancestor", "HEAD", remote_sha)[0] == 0
        if fast_forward:
            code, out = git("log", "--pretty=%h %s", "-n", "8", f"HEAD..{remote_sha}", timeout=30)
            incoming = [
                {"short": line.split(" ", 1)[0], "subject": line.split(" ", 1)[1] if " " in line else ""}
                for line in out.splitlines() if code == 0 and line.strip()
            ]

    notes = readiness(tools, state_ok)
    if not state_mounted:
        notes.append(f"状态目录不是挂载卷：{STATE_DIR}（重建容器后回滚点会丢失）")
    if dirty:
        notes.append("项目目录有未提交的本地改动：无法快进合并，更新会失败")
    if behind and not fast_forward:
        notes.append("本地提交与远端已分叉：需要人工处理，更新会失败")

    point = rollback_point()
    previous_images = {f"{name}_previous": image_state(previous_ref(ref)) for name, ref in images.items()}
    return {
        "updater": {"version": VERSION, "time": now_iso(), "busy": busy, "ready": not notes},
        "notes": notes,
        "project": {
            "dir": str(PROJECT_DIR),
            "branch": BRANCH,
            "commit": local,
            "dirty": dirty,
            "remote": remote,
            "behind": behind,
            "fast_forward": fast_forward,
            "update_available": bool(remote_sha) and remote_sha != local["sha"],
            "incoming": incoming,
        },
        "env": {
            "internal_db": uses_internal_db(),
            "web_port": env_file_values().get("WEB_PORT", "8089"),
            "state_dir": str(STATE_DIR),
            "health_urls": [url for _, url in health_targets()],
        },
        "services": service_states(),
        "images": {**images, **previous_images},
        "tools": tools,
        "last_result": last_result(),
        "rollback": {
            "available": bool(point.get("before_sha")),
            "commit": point.get("before_sha", ""),
            "short": str(point.get("before_sha") or "")[:7],
            "at": point.get("at", ""),
            "images": point.get("images", {}),
        },
    }


# --------------------------------------------------------------------------- 更新 / 回滚动作

BACKUP_TIMEOUT = 1800
PULL_TIMEOUT = 1800
MERGE_TIMEOUT = 300
UP_TIMEOUT = 900
POLL_SECONDS = 5


def health_targets() -> list[tuple[str, str]]:
    """探活目标：先走 nginx（一次覆盖前端进程、反向代理、后端与数据库），再直连后端。"""
    return [("前端 nginx", "http://frontend/"), ("后端接口", "http://backend:8089/api/health")]


def probe(url: str, timeout: int = 8) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return int(response.status), response.read(4096).decode("utf-8", "replace").strip()[:300]
    except Exception as exc:  # HTTPError / URLError / 超时都算探测失败，用 code 区分状态
        return int(getattr(exc, "code", 0) or 0), str(exc)[:300]


def wait_healthy(job: Job, timeout: int = HEALTH_TIMEOUT) -> tuple[bool, dict]:
    """轮询到全部目标返回 2xx 或超时。返回 (是否通过, 各目标最后一次探测结果)。"""
    deadline = time.time() + timeout
    report: dict = {}
    attempt = 0
    while True:
        attempt += 1
        healthy = True
        for label, url in health_targets():
            status, body = probe(url)
            ok = 200 <= status < 300
            report[label] = {"url": url, "status": status, "ok": ok, "detail": body, "attempt": attempt}
            healthy = healthy and ok
        summary = "、".join(f"{label} {item['status'] or '无响应'}" for label, item in report.items())
        if healthy:
            job.add(f"健康检查通过（第 {attempt} 次探测）：{summary}")
            return True, report
        if time.time() >= deadline:
            job.add(f"健康检查未通过（{timeout}s 超时，共 {attempt} 次探测）：{summary}")
            return False, report
        if attempt == 1 or attempt % 3 == 0:
            job.add(f"等待服务就绪…第 {attempt} 次探测：{summary}")
        time.sleep(POLL_SECONDS)


def run_backup(job: Job) -> bool:
    script = PROJECT_DIR / "scripts" / "backup.sh"
    if not script.is_file():
        job.add("[警告] scripts/backup.sh 不存在，跳过数据备份")
        return True
    return stream(job, ["bash", str(script)], timeout=BACKUP_TIMEOUT) == 0


def do_update(job: Job) -> None:
    job.add(f"项目目录：{PROJECT_DIR}")
    job.add(f"分支：{BRANCH}")
    # 1 检查版本
    job.begin(1)
    before = commit_info("HEAD")
    job.add(f"当前版本：{before['short'] or '未知'} {before['subject']}")
    remote = fetch_remote()
    if remote.get("error"):
        job.fail(f"无法获取远端代码：{remote['error']}", step="检查版本")
        return
    remote_sha = str(remote["sha"])
    if remote_sha == before["sha"]:
        job.add(f"远端代码与本地一致（{remote_sha[:7]}），本次只同步镜像")
    else:
        if git("merge-base", "--is-ancestor", "HEAD", remote_sha)[0] != 0:
            job.fail("本地提交与远端已分叉，无法快进合并：请人工处理后再更新",
                     before=before["short"], remote=remote_sha[:7])
            return
        code, out = git("rev-list", "--count", f"HEAD..{remote_sha}", timeout=30)
        job.add(f"远端有新提交：{remote_sha[:7]} {remote['subject']}（本地落后 {out.strip() or '?'} 个提交）")
    # 2 备份数据
    job.begin(2)
    if not run_backup(job):
        job.fail("数据备份失败，已中止更新（不做无备份的变更）", step="备份数据")
        return
    # 3 拉取代码
    job.begin(3)
    if remote_sha == before["sha"]:
        job.add("代码无变化，跳过合并")
    elif stream(job, ["git", "merge", "--ff-only", remote_sha], timeout=MERGE_TIMEOUT) != 0:
        job.fail("合并远端代码失败：项目目录可能有本地改动，请在服务器上查看 git status",
                 before=before["short"], remote=remote_sha[:7])
        return
    after = commit_info("HEAD")
    job.add(f"当前版本：{after['short']} {after['subject']}")
    # 4 拉取镜像
    job.begin(4)
    images = compose_images()
    running_before = {name: running_image_id(name) for name in PROJECT_SERVICES}
    tagged: dict = {}
    for name in PROJECT_SERVICES:
        ref = images.get(name, "")
        current = running_before.get(name, "")
        if not ref or not current:
            job.add(f"[警告] {name} 缺少当前镜像信息，本次不记录它的回滚点")
            continue
        code, out = capture(["docker", "tag", current, previous_ref(ref)], timeout=60)
        tagged[name] = code == 0
        if code == 0:
            job.add(f"{name} 当前镜像已标记为 {previous_ref(ref)}")
        else:
            job.add(f"[警告] {name} 标记回滚镜像失败：{out}")
    save_rollback_point({
        "before_sha": before["sha"],
        "branch": BRANCH,
        "at": now_iso(),
        "images": {
            name: {
                "ref": images.get(name, ""),
                "previous": previous_ref(images.get(name, "")) if images.get(name) else "",
                "id": running_before.get(name, ""),
                "tagged": bool(tagged.get(name)),
            }
            for name in PROJECT_SERVICES
        },
    })
    job.add("回滚点已保存（回滚 = 代码回到本提交 + 镜像换回上一版）")
    if stream(job, compose("pull", *PROJECT_SERVICES), timeout=PULL_TIMEOUT) != 0:
        job.fail("拉取镜像失败：确认 GHCR 包为 public，私有包需先执行 docker login ghcr.io；"
                 "容器仍在旧镜像上运行，可重试或回滚",
                 before=before["short"], after=after["short"])
        return
    # 5 重启服务
    job.begin(5)
    profiles, services = compose_scope()
    job.add(f"维护服务：{'、'.join(services)}（updater 不在其中，不会重建自己）")
    if stream(job, compose("up", "-d", *services, profiles=profiles), timeout=UP_TIMEOUT) != 0:
        job.fail("重建服务失败，可在本页点击回滚", before=before["short"], after=after["short"])
        return
    # 6 健康检查
    job.begin(6)
    healthy, report = wait_healthy(job)
    new_images = {name: image_state(images.get(name, "")).get("short", "") for name in PROJECT_SERVICES}
    detail = {
        "before": before["short"] or "-",
        "after": after["short"] or "-",
        "images": "、".join(f"{name}:{value or '-'}" for name, value in new_images.items()),
    }
    if not healthy:
        failed = "、".join(f"{label} {item['status'] or '无响应'}" for label, item in report.items() if not item["ok"])
        job.fail(f"服务已重建但健康检查未通过（{failed}）；请查看容器日志，必要时在本页回滚", **detail)
        return
    job.finish("ok", f"已更新到 {after['short'] or before['short']}（{before['short'] or '?'} → {after['short'] or '?'}）", **detail)


def do_rollback(job: Job) -> None:
    # 1 读取回滚点
    job.begin(1)
    point = rollback_point()
    target = str(point.get("before_sha") or "")
    if not target:
        job.fail("没有可用的回滚点：只能回滚「在本页执行过更新」的版本，更早的版本请用备份恢复")
        return
    current = commit_info("HEAD")
    job.add(f"回滚点：{target[:7]}（记录于 {point.get('at') or '未知时间'}）")
    job.add(f"当前版本：{current['short']} {current['subject']}")
    # 2 恢复代码
    job.begin(2)
    if current["sha"] == target:
        job.add("代码已在该提交上，跳过")
    elif stream(job, ["git", "reset", "--hard", target], timeout=MERGE_TIMEOUT) != 0:
        job.fail("恢复代码失败，请在服务器上人工处理", target=target[:7])
        return
    # 3 恢复镜像
    job.begin(3)
    images = compose_images()
    for name in PROJECT_SERVICES:
        entry = (point.get("images") or {}).get(name) or {}
        ref = str(entry.get("ref") or images.get(name) or "")
        if not ref:
            job.add(f"[警告] 回滚点里没有 {name} 的镜像信息，保持现状")
            continue
        previous = str(entry.get("previous") or previous_ref(ref))
        source = previous if image_state(previous)["present"] else str(entry.get("id") or "")
        if not source:
            job.add(f"[警告] {name} 找不到上一版镜像（可能已被清理），保持现状")
            continue
        code, out = capture(["docker", "tag", source, ref], timeout=60)
        if code == 0:
            job.add(f"{name} 镜像已换回 {source}")
        else:
            job.add(f"[错误] {name} 镜像回滚失败：{out}")
    # 4 重启服务
    job.begin(4)
    profiles, services = compose_scope()
    job.add(f"维护服务：{'、'.join(services)}")
    if stream(job, compose("up", "-d", *services, profiles=profiles), timeout=UP_TIMEOUT) != 0:
        job.fail("重建服务失败，请在服务器上人工处理", target=target[:7])
        return
    # 5 健康检查
    job.begin(5)
    healthy, report = wait_healthy(job)
    if not healthy:
        failed = "、".join(f"{label} {item['status'] or '无响应'}" for label, item in report.items() if not item["ok"])
        job.fail(f"已回滚到 {target[:7]}，但健康检查未通过（{failed}）；请查看容器日志", target=target[:7])
        return
    clear_rollback_point()  # 回滚点是一次性的：用过就清掉，避免重复回滚到更早的版本
    job.finish("ok", f"已回滚到 {target[:7]}", before=current["short"] or "-", after=target[:7])


# --------------------------------------------------------------------------- HTTP 服务


class Handler(BaseHTTPRequestHandler):
    """固定接口：GET /health、GET /status、GET /job、POST /update、POST /rollback。"""

    server_version = f"temubox-updater/{VERSION}"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # 默认实现每个请求打两行，容器日志太吵
        sys.stderr.write(f"[{now_iso()}] {self.address_string()} {fmt % args}\n")

    # --- 输出 ----------------------------------------------------------------
    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self, query: dict) -> bool:
        if not TOKEN:
            self.send_json(503, {"ok": False, "error": "边车未配置 UPDATER_TOKEN，拒绝所有请求"})
            return False
        supplied = self.headers.get("X-Updater-Token") or (query.get("token") or [""])[0]
        # 长度不等时 compare_digest 会直接返回 False，不会抛异常，这里包一层只为可读
        if len(supplied) != len(TOKEN) or not hmac.compare_digest(supplied, TOKEN):
            self.send_json(401, {"ok": False, "error": "鉴权失败：更新令牌不正确"})
            return False
        return True

    # --- 路由 ----------------------------------------------------------------
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        if path == "/health":  # 供 compose healthcheck 使用：不带令牌、只回最基本信息
            self.send_json(200, {"status": "ok", "version": VERSION, "configured": bool(TOKEN)})
            return
        if not self.authorized(query):
            return
        if path == "/status":
            job = current_job()
            self.send_json(200, {"ok": True, "data": status_snapshot(), "job": job.snapshot(0) if job else None})
            return
        if path == "/job":
            raw = (query.get("since") or ["0"])[0]
            try:
                since = max(0, int(raw))
            except ValueError:
                since = 0
            job = current_job()
            self.send_json(200, {
                "ok": True,
                "job": job.snapshot(since) if job else None,
                "last_result": last_result(),
                "busy": bool(job and job.status == "running"),
            })
            return
        self.send_json(404, {"ok": False, "error": f"未知接口：{path}"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        if not self.authorized(query):
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            self.send_json(413, {"ok": False, "error": "请求体过大：更新接口不接受任何参数"})
            return
        body = self.rfile.read(length).decode("utf-8", "replace").strip() if length > 0 else ""
        if body not in ("", "{}"):
            self.send_json(400, {"ok": False, "error": "更新接口不接受任何参数"})
            return
        kind = path.strip("/")
        if kind not in ("update", "rollback"):
            self.send_json(404, {"ok": False, "error": f"未知接口：{path}"})
            return
        job, message = start_job(kind)
        if job is None:
            self.send_json(409, {"ok": False, "error": message})
            return
        self.send_json(202, {"ok": True, "job": job.snapshot(0)})


def main() -> int:
    if not TOKEN:
        print("[错误] 未配置 UPDATER_TOKEN，边车拒绝启动："
              "docker.sock 权限太大，不能挂在无鉴权的 HTTP 服务后面", flush=True)
        return 1
    if not (PROJECT_DIR / "docker-compose.yml").is_file():
        print(f"[错误] 找不到 {PROJECT_DIR / 'docker-compose.yml'}："
              "PROJECT_DIR 必须挂到容器内的同一绝对路径", flush=True)
        return 1
    state_ok, state_mounted = state_dir_usable()
    if not state_ok:
        print(f"[错误] 状态目录不可写：{STATE_DIR}", flush=True)
        return 1
    if not state_mounted:
        print(f"[警告] 状态目录 {STATE_DIR} 不是挂载卷，容器重建后回滚点与任务结果会丢失", flush=True)
    tools = tool_versions()
    missing = [name for name, value in tools.items() if not value]
    if missing:
        print(f"[警告] 缺少工具：{'、'.join(missing)}", flush=True)
    print(f"Temu-Box updater {VERSION} 监听 {HOST}:{PORT}", flush=True)
    print(f"项目目录：{PROJECT_DIR}｜分支：{BRANCH}｜"
          f"数据库：{'compose 内置' if uses_internal_db() else '外部'}", flush=True)
    for name, value in tools.items():
        print(f"  {name}: {value or '未检测到'}", flush=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("收到中断信号，退出", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
