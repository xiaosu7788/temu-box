"""版本号与更新日志。

唯一来源是仓库根目录的 `VERSION` 与 `CHANGELOG.md`：

- 镜像构建时（docker/backend.Dockerfile）把这两个文件拷到 `/app` 下；
- 本地开发时回退到仓库根目录，不需要额外配置。

解析逻辑与 `docker/updater/server.py` 里的同名函数保持一致——边车是独立镜像，
无法复用这里的代码，改解析规则时两边都要改。
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

VERSION_FILE = Path(os.environ.get("TEMUBOX_VERSION_FILE", "/app/VERSION"))
CHANGELOG_FILE = Path(os.environ.get("TEMUBOX_CHANGELOG_FILE", "/app/CHANGELOG.md"))

# 接口最多返回多少个版本的日志
MAX_VERSIONS = 20

_VERSION_HEAD = re.compile(r"^##\s+(?P<version>\d[^\s]*)\s*(?:[-—–]\s*(?P<date>\d{4}-\d{2}-\d{2}))?\s*$")
_GROUP_HEAD = re.compile(r"^###\s+(?P<title>\S.*?)\s*$")
_ITEM = re.compile(r"^[-*]\s+(?P<text>\S.*?)\s*$")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _text(path: Path, fallback_name: str) -> str:
    """先读容器内的拷贝，读不到再回退到仓库根目录（本地开发）。"""
    return _read(path) or _read(REPO_ROOT / fallback_name)


def parse_changelog(text: str, limit: int = MAX_VERSIONS) -> list[dict]:
    """把 CHANGELOG.md 解析成 [{version, date, groups: [{title, items: [...]}]}]，保持文件顺序（新 → 旧）。"""
    entries: list[dict] = []
    current: dict | None = None
    group: dict | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        head = _VERSION_HEAD.match(line)
        if head:
            current = {"version": head.group("version"), "date": head.group("date") or "", "groups": []}
            entries.append(current)
            group = None
            continue
        if current is None:
            continue
        group_head = _GROUP_HEAD.match(line)
        if group_head:
            group = {"title": group_head.group("title"), "items": []}
            current["groups"].append(group)
            continue
        item = _ITEM.match(line.strip())
        if item is not None and group is not None:
            group["items"].append(item.group("text"))
    result: list[dict] = []
    for entry in entries:
        groups = [g for g in entry["groups"] if g["items"]]
        if groups:
            result.append({"version": entry["version"], "date": entry["date"], "groups": groups})
    return result[:limit]


@lru_cache(maxsize=1)
def version() -> str:
    text = _text(VERSION_FILE, "VERSION").strip()
    return text.splitlines()[0].strip() if text else "0.0.0"


@lru_cache(maxsize=1)
def commit() -> str:
    """镜像构建时由 CI 通过 --build-arg GIT_SHA 传入；本地开发为空。"""
    return os.environ.get("TEMUBOX_GIT_SHA", "").strip()[:7]


@lru_cache(maxsize=1)
def changelog() -> list[dict]:
    return parse_changelog(_text(CHANGELOG_FILE, "CHANGELOG.md"))


def info() -> dict:
    entries = changelog()
    current = version()
    released = next((entry["date"] for entry in entries if entry["version"] == current), "")
    return {"version": current, "commit": commit(), "commit_date": released, "changelog": entries}
