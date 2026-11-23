"""Add retry columns to temu_kefu_actions.

平台自助工具执行失败（「系统忙碌中，请您稍后重试」）属于临时性错误，需要自动重试。
新增 retry_count / next_retry_at 两列支持退避重试。
"""
from alembic import op
import sqlalchemy as sa

revision = "20261117_01"
down_revision = "20261116_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("temu_kefu_actions"):
        return
    columns = {column["name"] for column in inspector.get_columns("temu_kefu_actions")}

    if "retry_count" not in columns:
        op.add_column(
            "temu_kefu_actions",
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        )
    if "next_retry_at" not in columns:
        op.add_column(
            "temu_kefu_actions",
            sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(
            "ix_temu_kefu_actions_next_retry_at",
            "temu_kefu_actions",
            ["next_retry_at"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("temu_kefu_actions"):
        return
    columns = {column["name"] for column in inspector.get_columns("temu_kefu_actions")}
    if "next_retry_at" in columns:
        indexes = {index["name"] for index in inspector.get_indexes("temu_kefu_actions")}
        if "ix_temu_kefu_actions_next_retry_at" in indexes:
            op.drop_index("ix_temu_kefu_actions_next_retry_at", table_name="temu_kefu_actions")
        with op.batch_alter_table("temu_kefu_actions") as batch:
            batch.drop_column("next_retry_at")
    if "retry_count" in columns:
        with op.batch_alter_table("temu_kefu_actions") as batch:
            batch.drop_column("retry_count")
