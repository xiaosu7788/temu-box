"""Add temu_kefu_batches table and actions.batch_id.

批量执行客服工具（下架 / 发布 / 价格刷新）：
一批编号 → 后台线程串行「查商品 + 拦截检查 + 提交」→ 前端轮询进度。
"""
from alembic import op
import sqlalchemy as sa

revision = "20261116_01"
down_revision = "20261115_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    if not inspector.has_table("temu_kefu_batches"):
        op.create_table(
            "temu_kefu_batches",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("account_masked", sa.String(120), nullable=False, server_default=""),
            sa.Column("tool_id", sa.String(32), nullable=False, server_default=""),
            sa.Column("tool_name", sa.String(80), nullable=False, server_default=""),
            sa.Column("data_type_name", sa.String(8), nullable=False, server_default="SKC"),
            sa.Column("reason_code", sa.Integer(), nullable=True),
            sa.Column("reason_detail", sa.String(255), nullable=False, server_default=""),
            sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
            sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("succeeded", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("skipped", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error", sa.String(255), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_temu_kefu_batches_user_id", "temu_kefu_batches", ["user_id"])
        op.create_index("ix_temu_kefu_batches_status", "temu_kefu_batches", ["status"])
        op.create_index("ix_temu_kefu_batches_created_at", "temu_kefu_batches", ["created_at"])

    if inspector.has_table("temu_kefu_actions"):
        columns = {column["name"] for column in inspector.get_columns("temu_kefu_actions")}
        if "batch_id" not in columns:
            op.add_column("temu_kefu_actions", sa.Column("batch_id", sa.String(64), nullable=True))
        indexes = {index["name"] for index in inspector.get_indexes("temu_kefu_actions")}
        if "ix_temu_kefu_actions_batch_id" not in indexes:
            op.create_index("ix_temu_kefu_actions_batch_id", "temu_kefu_actions", ["batch_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    if inspector.has_table("temu_kefu_actions"):
        indexes = {index["name"] for index in inspector.get_indexes("temu_kefu_actions")}
        if "ix_temu_kefu_actions_batch_id" in indexes:
            op.drop_index("ix_temu_kefu_actions_batch_id", table_name="temu_kefu_actions")
        columns = {column["name"] for column in inspector.get_columns("temu_kefu_actions")}
        if "batch_id" in columns:
            with op.batch_alter_table("temu_kefu_actions") as batch:
                batch.drop_column("batch_id")

    if inspector.has_table("temu_kefu_batches"):
        op.drop_index("ix_temu_kefu_batches_created_at", table_name="temu_kefu_batches")
        op.drop_index("ix_temu_kefu_batches_status", table_name="temu_kefu_batches")
        op.drop_index("ix_temu_kefu_batches_user_id", table_name="temu_kefu_batches")
        op.drop_table("temu_kefu_batches")
