"""迁移链自检：从空库跑 `alembic upgrade head`。

生产环境 `TEMUBOX_AUTO_CREATE_TABLES=0`，表结构完全由 Alembic 负责。
其余测试都基于 `create_all`，不会执行迁移脚本，所以下面这些问题只能靠这个用例拦住：

- 迁移链断掉 / revision 写错
- `sa.Column(..., index=True)` 与 `op.create_index` 建同名索引（DuplicateTable）
- 新增模型没配迁移（升级后启动报 column xxx does not exist）
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture()
def alembic_env(tmp_path: Path) -> dict:
    env = dict(os.environ)
    db = tmp_path / "migrate.db"
    env["DATABASE_URL"] = "sqlite:///" + db.as_posix()
    env["TEMUBOX_DATA_DIR"] = str(tmp_path)
    # 迁移测试要自己建表，不能让 create_all 抢先
    env["TEMUBOX_AUTO_CREATE_TABLES"] = "0"
    return env


def _alembic(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
    )


def test_upgrade_head_from_empty(alembic_env: dict) -> None:
    """空库能一路升到 head。"""
    result = _alembic(alembic_env, "upgrade", "head")
    assert result.returncode == 0, f"升级失败：\n{result.stdout}\n{result.stderr}"

    current = _alembic(alembic_env, "current")
    assert current.returncode == 0, current.stderr
    assert "(head)" in current.stdout, current.stdout


def test_upgrade_is_idempotent(alembic_env: dict) -> None:
    """重复执行 upgrade head 不应该报错（部署重跑、stamp 后重进都靠这个）。"""
    assert _alembic(alembic_env, "upgrade", "head").returncode == 0
    again = _alembic(alembic_env, "upgrade", "head")
    assert again.returncode == 0, f"重复升级失败：\n{again.stdout}\n{again.stderr}"


def test_models_have_migrations(alembic_env: dict) -> None:
    """所有模型表在迁移后都应存在，防止「加了模型忘了写迁移」。"""
    assert _alembic(alembic_env, "upgrade", "head").returncode == 0

    import sqlalchemy as sa

    from app.database import Base, engine

    db_url = alembic_env["DATABASE_URL"]
    check_engine = sa.create_engine(db_url)
    inspector = sa.inspect(check_engine)
    existing = set(inspector.get_table_names())

    from app import database as db_module

    expected = set(Base.metadata.tables.keys())
    missing = sorted(expected - existing)
    assert not missing, f"这些表模型里有、迁移里没有：{missing}"
    check_engine.dispose()
    engine.dispose()
