"""Add managed inventory categories and isolate half-headcost by inventory class."""
from alembic import op
import sqlalchemy as sa

revision = "20260915_02"
down_revision = "20260915_01"
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set:
    if not inspector.has_table(table):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("inventory_category_defs"):
        op.create_table(
            "inventory_category_defs",
            sa.Column("key", sa.String(16), primary_key=True),
            sa.Column("label", sa.String(80), nullable=False),
            sa.Column("mode", sa.String(16), nullable=False, server_default="auto"),
            sa.Column("code_pattern", sa.String(255), nullable=True),
            sa.Column("price_max", sa.Float(), nullable=False, server_default="100"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        bind.execute(sa.text(
            "INSERT INTO inventory_category_defs (key, label, mode, price_max, enabled, sort_order) "
            "VALUES ('A', 'A类目', 'mapped', 100, true, 10), ('B', 'B类目', 'auto', 100, true, 20)"
        ))

    if "inventory_category" not in _columns(inspector, "half_headcost_skus"):
        # Rebuild the table so the new dimension participates in the primary key.
        op.rename_table("half_headcost_skus", "half_headcost_skus_legacy_inventory")
        op.create_table(
            "half_headcost_skus",
            sa.Column("category_id", sa.Integer(), primary_key=True),
            sa.Column("sku", sa.String(255), primary_key=True),
            sa.Column("inventory_category", sa.String(16), primary_key=True, nullable=False, server_default="A"),
            sa.Column("set_type", sa.String(64), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
        bind.execute(sa.text(
            "INSERT INTO half_headcost_skus (category_id, sku, inventory_category, set_type, updated_at) "
            "SELECT category_id, sku, 'A', set_type, updated_at FROM half_headcost_skus_legacy_inventory"
        ))
        op.drop_table("half_headcost_skus_legacy_inventory")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "inventory_category" in _columns(inspector, "half_headcost_skus"):
        op.rename_table("half_headcost_skus", "half_headcost_skus_new_inventory")
        op.create_table(
            "half_headcost_skus",
            sa.Column("category_id", sa.Integer(), primary_key=True),
            sa.Column("sku", sa.String(255), primary_key=True),
            sa.Column("set_type", sa.String(64), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
        bind.execute(sa.text(
            "INSERT INTO half_headcost_skus (category_id, sku, set_type, updated_at) "
            "SELECT category_id, sku, set_type, updated_at FROM half_headcost_skus_new_inventory"
        ))
        op.drop_table("half_headcost_skus_new_inventory")
    if inspector.has_table("inventory_category_defs"):
        op.drop_table("inventory_category_defs")
