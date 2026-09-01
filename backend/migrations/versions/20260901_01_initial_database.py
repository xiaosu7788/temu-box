"""Create initial application tables."""
from alembic import op
import sqlalchemy as sa

revision = "20260901_01"
down_revision = None
branch_labels = None
depends_on = None


def _missing(name: str) -> bool:
    return not sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if _missing("inventory_versions"):
        op.create_table("inventory_versions", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("source_path", sa.String(1024), nullable=False), sa.Column("file_size", sa.Integer(), nullable=False), sa.Column("mtime_ns", sa.BigInteger(), nullable=False), sa.Column("parser_version", sa.Integer(), nullable=False), sa.Column("sku_count", sa.Integer(), nullable=False), sa.Column("is_current", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    if _missing("inventory_items"):
        op.create_table("inventory_items", sa.Column("sku", sa.String(255), primary_key=True), sa.Column("price", sa.Float()), sa.Column("set_type", sa.String(64)), sa.Column("source_sheet", sa.String(255)), sa.Column("source_row", sa.Integer()), sa.Column("source_column", sa.Integer()), sa.Column("inventory_version_id", sa.Integer()), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    if _missing("half_headcost_skus"):
        op.create_table("half_headcost_skus", sa.Column("sku", sa.String(255), primary_key=True), sa.Column("set_type", sa.String(64), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    if _missing("tasks"):
        op.create_table("tasks", sa.Column("id", sa.String(64), primary_key=True), sa.Column("status", sa.String(32), nullable=False), sa.Column("created_at", sa.String(32), nullable=False), sa.Column("payload", sa.Text(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    if _missing("activity_jobs"):
        op.create_table("activity_jobs", sa.Column("id", sa.String(64), primary_key=True), sa.Column("status", sa.String(32), nullable=False), sa.Column("filename", sa.String(255), nullable=False), sa.Column("output_path", sa.String(1024)), sa.Column("stats", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))


def downgrade() -> None:
    for table in ("activity_jobs", "tasks", "half_headcost_skus", "inventory_items", "inventory_versions"):
        if not _missing(table):
            op.drop_table(table)
