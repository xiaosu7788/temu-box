"""Add inventory category binding and category-aware inventory tables.

- categories.inventory_category: 库存类目绑定（NULL = 默认 A）
- inventory_versions.category / inventory_items.category: 库存数据按类目隔离（存量归 A）
"""
from alembic import op
import sqlalchemy as sa

revision = "20260915_01"
down_revision = "20260908_02"
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set:
    if not inspector.has_table(table):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    if "inventory_category" not in _columns(inspector, "categories"):
        op.add_column("categories", sa.Column("inventory_category", sa.String(16), nullable=True))

    if "category" not in _columns(inspector, "inventory_versions"):
        op.add_column(
            "inventory_versions",
            sa.Column("category", sa.String(16), nullable=False, server_default="A"),
        )
    if "category" not in _columns(inspector, "inventory_items"):
        op.add_column(
            "inventory_items",
            sa.Column("category", sa.String(16), nullable=False, server_default="A"),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "inventory_category" in _columns(inspector, "categories"):
        op.drop_column("categories", "inventory_category")
    if "category" in _columns(inspector, "inventory_versions"):
        op.drop_column("inventory_versions", "category")
    if "category" in _columns(inspector, "inventory_items"):
        op.drop_column("inventory_items", "category")
