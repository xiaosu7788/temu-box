"""Add temu_kefu_actions table.

客服写操作（下架 / 发布到站点 / 价格刷新）的事务记录：
创建时一次性完成「查商品 + 拦截检查 + 组装报文」并落库，确认阶段只发送已存的报文，
避免重新拦截（preIntercept 有状态，同一商品只能成功一次）。
"""
from alembic import op
import sqlalchemy as sa

revision = "20261115_01"
down_revision = "20261114_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("temu_kefu_actions"):
        return
    op.create_table(
        "temu_kefu_actions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=True, index=True),
        sa.Column("account_masked", sa.String(120), nullable=False, server_default=""),
        sa.Column("tool_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("tool_name", sa.String(80), nullable=False, server_default=""),
        sa.Column("data_type", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("data_type_name", sa.String(8), nullable=False, server_default="SKC"),
        sa.Column("data_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("reason_code", sa.Integer(), nullable=True),
        sa.Column("reason_detail", sa.String(255), nullable=False, server_default=""),
        sa.Column("stage", sa.String(20), nullable=False, server_default="preview", index=True),
        sa.Column("preview", sa.Text(), nullable=True),
        sa.Column("product_snapshot", sa.Text(), nullable=True),
        sa.Column("parent_msg_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("intercept_code", sa.Integer(), nullable=True),
        sa.Column("intercept_message", sa.String(255), nullable=False, server_default=""),
        sa.Column("request_body", sa.Text(), nullable=True),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("msg_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("robot_reply", sa.Text(), nullable=True),
        sa.Column("error", sa.String(255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_temu_kefu_actions_user_id", "temu_kefu_actions", ["user_id"])
    op.create_index("ix_temu_kefu_actions_stage", "temu_kefu_actions", ["stage"])
    op.create_index("ix_temu_kefu_actions_created_at", "temu_kefu_actions", ["created_at"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("temu_kefu_actions"):
        return
    op.drop_index("ix_temu_kefu_actions_created_at", table_name="temu_kefu_actions")
    op.drop_index("ix_temu_kefu_actions_stage", table_name="temu_kefu_actions")
    op.drop_index("ix_temu_kefu_actions_user_id", table_name="temu_kefu_actions")
    op.drop_table("temu_kefu_actions")
