"""Add audit logs for security and administrative operations."""
from alembic import op
import sqlalchemy as sa


revision = "20260903_01"
down_revision = "20260902_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("audit_logs"):
        op.create_table(
            "audit_logs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("actor_id", sa.Integer(), nullable=True),
            sa.Column("actor_username", sa.String(80), nullable=False, server_default=""),
            sa.Column("action", sa.String(64), nullable=False),
            sa.Column("target_type", sa.String(32), nullable=False, server_default=""),
            sa.Column("target_id", sa.String(128), nullable=False, server_default=""),
            sa.Column("detail", sa.Text(), nullable=False, server_default=""),
            sa.Column("ip", sa.String(64), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_audit_logs_actor_id", "audit_logs", ["actor_id"])
        op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
        op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("audit_logs"):
        op.drop_table("audit_logs")
