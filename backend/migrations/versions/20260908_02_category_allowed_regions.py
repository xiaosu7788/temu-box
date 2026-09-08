"""Add Category.allowed_regions for per-region category availability.

NULL (default) keeps existing behavior: category open to all regions.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260908_02"
down_revision = "20260908_01"
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set:
    if not inspector.has_table(table):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "allowed_regions" not in _columns(inspector, "categories"):
        op.add_column("categories", sa.Column("allowed_regions", sa.Text(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "allowed_regions" in _columns(inspector, "categories"):
        op.drop_column("categories", "allowed_regions")
