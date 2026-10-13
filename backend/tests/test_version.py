from __future__ import annotations

from fastapi.testclient import TestClient

from app.database import create_user
from app.main import app
from app.services.auth import hash_password
from app.version import REPO_ROOT, parse_changelog, version

SAMPLE = """# 更新日志

开头说明文字，不属于任何版本。

## 1.2.0 - 2026-05-01

### 新增
- 第一条
- 第二条

### 修复
- 修了一个 bug

## 1.1.0 - 2026-04-01

### 优化
- 快了一点

## 1.0.0

### 新增
- 没有写日期的版本
"""


def test_parse_changelog_groups_and_dates():
    entries = parse_changelog(SAMPLE)
    assert [entry["version"] for entry in entries] == ["1.2.0", "1.1.0", "1.0.0"]
    assert entries[0]["date"] == "2026-05-01"
    assert entries[2]["date"] == ""
    assert [group["title"] for group in entries[0]["groups"]] == ["新增", "修复"]
    assert entries[0]["groups"][0]["items"] == ["第一条", "第二条"]


def test_parse_changelog_skips_empty_groups_and_respects_limit():
    assert parse_changelog("## 1.0.0 - 2026-01-01\n\n### 新增\n") == []
    assert parse_changelog("# 只有标题\n\n随便写点东西\n") == []
    assert len(parse_changelog(SAMPLE, limit=2)) == 2


def test_version_file_is_single_source():
    text = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert text, "仓库根目录必须有 VERSION 文件"
    assert version() == text.splitlines()[0].strip()
    assert version().count(".") == 2


def test_changelog_covers_current_version():
    entries = parse_changelog((REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    assert entries
    assert entries[0]["version"] == version()
    for entry in entries:
        assert entry["date"], entry
        assert entry["groups"], entry


def test_version_endpoint_requires_admin():
    with TestClient(app) as client:
        create_user("version_admin", hash_password("verpass123"), role="admin", status="approved")
        create_user("version_reader", hash_password("verread123"), role="user", status="approved")

        assert client.get("/api/health").json()["version"] == version()

        assert client.post("/api/auth/login", json={"username": "version_reader", "password": "verread123"}).status_code == 200
        assert client.get("/api/version").status_code == 403

        assert client.post("/api/auth/login", json={"username": "version_admin", "password": "verpass123"}).status_code == 200
        payload = client.get("/api/version").json()
        assert payload["version"] == version()
        assert payload["changelog"][0]["version"] == version()
        assert isinstance(payload["commit"], str)
