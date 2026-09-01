"""Add user accounts and configurable cost settings."""
from alembic import op
import sqlalchemy as sa

revision = "20260901_02"
down_revision = "20260901_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("users"):
        op.create_table("users", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("username", sa.String(80), nullable=False, unique=True), sa.Column("password_hash", sa.String(255), nullable=False), sa.Column("display_name", sa.String(120), nullable=False), sa.Column("role", sa.String(20), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("approved_at", sa.DateTime(timezone=True)))
    if not inspector.has_table("app_settings"):
        op.create_table("app_settings", sa.Column("key", sa.String(120), primary_key=True), sa.Column("value", sa.Text(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    if inspector.has_table("tasks") and "owner_id" not in {column["name"] for column in inspector.get_columns("tasks")}:
        op.add_column("tasks", sa.Column("owner_id", sa.Integer(), nullable=True))
    if inspector.has_table("activity_jobs") and "owner_id" not in {column["name"] for column in inspector.get_columns("activity_jobs")}:
        op.add_column("activity_jobs", sa.Column("owner_id", sa.Integer(), nullable=True))
    activity_columns = {column["name"] for column in inspector.get_columns("activity_jobs")} if inspector.has_table("activity_jobs") else set()
    for name, column in (("progress", sa.Integer(),), ("message", sa.String(255)), ("logs", sa.Text())):
        if inspector.has_table("activity_jobs") and name not in activity_columns:
            op.add_column("activity_jobs", sa.Column(name, column, nullable=False, server_default="0" if name == "progress" else ("" if name == "message" else "[]")))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("tasks") and "owner_id" in {column["name"] for column in inspector.get_columns("tasks")}:
        op.drop_column("tasks", "owner_id")
    if inspector.has_table("activity_jobs") and "owner_id" in {column["name"] for column in inspector.get_columns("activity_jobs")}:
        op.drop_column("activity_jobs", "owner_id")
    activity_columns = {column["name"] for column in inspector.get_columns("activity_jobs")} if inspector.has_table("activity_jobs") else set()
    for name in ("logs", "message", "progress"):
        if inspector.has_table("activity_jobs") and name in activity_columns:
            op.drop_column("activity_jobs", name)
    if inspector.has_table("app_settings"):
        op.drop_table("app_settings")
    if inspector.has_table("users"):
        op.drop_table("users")
