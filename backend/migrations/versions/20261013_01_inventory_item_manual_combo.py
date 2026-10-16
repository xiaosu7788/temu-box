"""Add manual_combo flag to inventory_items.

手动组合标记：勾选后订单成本直接用库存登记的货值（不再加头程），导出汇总表时货值列显示 /。
"""
from alembic import op
import sqlalchemy as sa

revision = "20261013_01"
down_revision = "20260915_03"
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set:
    if not inspector.has_table(table):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "manual_combo" in _columns(inspector, "inventory_items"):
        return
    op.add_column(
        "inventory_items",
        sa.Column("manual_combo", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "manual_combo" not in _columns(inspector, "inventory_items"):
        return
    with op.batch_alter_table("inventory_items") as batch:
        batch.drop_column("manual_combo")
