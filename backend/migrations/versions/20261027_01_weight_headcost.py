"""Add weight_headcost_skus table.

克重计算头程名单：编号 → 克重(g)，头程 = 克重 × 0.017。
"""
from alembic import op
import sqlalchemy as sa

revision = "20261027_01"
down_revision = "20261017_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("weight_headcost_skus"):
        return
    op.create_table(
        "weight_headcost_skus",
        sa.Column("category_id", sa.Integer(), primary_key=True),
        sa.Column("sku", sa.String(255), primary_key=True),
        sa.Column("inventory_category", sa.String(16), primary_key=True, nullable=False),
        sa.Column("weight_g", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_weight_headcost_skus_inventory_category", "weight_headcost_skus", ["inventory_category"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("weight_headcost_skus"):
        return
    op.drop_index("ix_weight_headcost_skus_inventory_category", table_name="weight_headcost_skus")
    op.drop_table("weight_headcost_skus")
