"""Add persistent inventory detail exclusions."""
from alembic import op
import sqlalchemy as sa

revision = "20260901_03"
down_revision = "20260901_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("inventory_exclusions"):
        op.create_table(
            "inventory_exclusions",
            sa.Column("sku", sa.String(255), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("inventory_exclusions"):
        op.drop_table("inventory_exclusions")
