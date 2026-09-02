"""Repair activity job columns for databases upgraded from an older migration."""
from alembic import op
import sqlalchemy as sa


revision = "20260902_01"
down_revision = "20260901_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("activity_jobs"):
        return

    columns = {column["name"] for column in inspector.get_columns("activity_jobs")}
    missing = {
        "progress": (sa.Integer(), "0"),
        "message": (sa.String(255), "''"),
        "logs": (sa.Text(), "'[]'"),
    }
    for name, (column_type, default) in missing.items():
        if name not in columns:
            op.add_column(
                "activity_jobs",
                sa.Column(name, column_type, nullable=False, server_default=sa.text(default)),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("activity_jobs"):
        return

    columns = {column["name"] for column in inspector.get_columns("activity_jobs")}
    for name in ("logs", "message", "progress"):
        if name in columns:
            op.drop_column("activity_jobs", name)
