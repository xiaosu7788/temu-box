"""模块权限、商品表单模板与建品日志/批量进度表

Revision ID: 20261123_01
Revises: 20261117_01
Create Date: 2026-11-23

这批表/列之前只靠 `create_all` + `_ensure_*_schema()` 兜底（本地开发默认开），
但生产 `TEMUBOX_AUTO_CREATE_TABLES=0`，完全依赖 Alembic，
所以升级后启动直接报 `column users.modules does not exist`。

全部操作都是幂等的（先 inspect 再执行），已在旧库上跑过也能安全重跑。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261123_01"
down_revision = "20261117_01"
branch_labels = None
depends_on = None


def _indexes(bind, table: str) -> set[str]:
    try:
        return {i["name"] for i in sa.inspect(bind).get_indexes(table)}
    except Exception:  # noqa: BLE001 - 表不存在时忽略
        return set()


def _add_index(bind, name: str, table: str, columns: list[str]) -> None:
    if name in _indexes(bind, table):
        return
    op.create_index(name, table, columns)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ---------- users: 模块权限 ----------
    if inspector.has_table("users"):
        columns = {c["name"] for c in inspector.get_columns("users")}
        if "modules" not in columns:
            op.add_column(
                "users",
                sa.Column("modules", sa.Text(), nullable=False, server_default="[]"),
            )
        if "store_modules" not in columns:
            op.add_column(
                "users",
                sa.Column("store_modules", sa.Text(), nullable=False, server_default="[]"),
            )

    # ---------- product_form_templates ----------
    if not inspector.has_table("product_form_templates"):
        op.create_table(
            "product_form_templates",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(120), nullable=False, server_default=""),
            sa.Column("note", sa.String(500), nullable=False, server_default=""),
            sa.Column("form_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("account_hash", sa.String(40), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
    else:
        # 表是 create_all 建的旧库，补上后来加的列
        columns = {c["name"] for c in inspector.get_columns("product_form_templates")}
        if "account_hash" not in columns:
            op.add_column(
                "product_form_templates",
                sa.Column("account_hash", sa.String(40), nullable=False, server_default=""),
            )
    _add_index(bind, "ix_product_form_templates_user_id", "product_form_templates", ["user_id"])
    _add_index(bind, "ix_product_form_templates_account_hash", "product_form_templates", ["account_hash"])

    # ---------- product_batch_states ----------
    if not inspector.has_table("product_batch_states"):
        op.create_table(
            "product_batch_states",
            sa.Column("user_id", sa.Integer(), primary_key=True),
            sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
    _add_index(bind, "ix_product_batch_states_updated_at", "product_batch_states", ["updated_at"])

    # ---------- product_create_logs ----------
    if not inspector.has_table("product_create_logs"):
        op.create_table(
            "product_create_logs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("product_name", sa.String(255), nullable=False, server_default=""),
            sa.Column("status", sa.String(12), nullable=False, server_default="ok"),
            sa.Column("error", sa.String(500), nullable=False, server_default=""),
            sa.Column("product_id", sa.BigInteger(), nullable=True),
            sa.Column("batch", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
    _add_index(bind, "ix_product_create_logs_user_id", "product_create_logs", ["user_id"])
    _add_index(bind, "ix_product_create_logs_created_at", "product_create_logs", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("product_create_logs"):
        op.drop_table("product_create_logs")
    if inspector.has_table("product_batch_states"):
        op.drop_table("product_batch_states")
    # product_form_templates 与 users 的新列保留：里面是用户数据，
    # 回滚代码不该顺手删掉，需要时手动处理。
