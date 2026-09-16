"""Make inventory_items unique per inventory category."""
from alembic import op
import sqlalchemy as sa

revision = "20260915_03"
down_revision = "20260915_02"
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set:
    if not inspector.has_table(table):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("inventory_items"):
        return
    pk = inspector.get_pk_constraint("inventory_items").get("constrained_columns") or []
    if set(pk) == {"sku", "category"}:
        return
    op.rename_table("inventory_items", "inventory_items_legacy_category")
    op.create_table(
        "inventory_items",
        sa.Column("sku", sa.String(255), primary_key=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("set_type", sa.String(64), nullable=True),
        sa.Column("source_sheet", sa.String(255), nullable=True),
        sa.Column("source_row", sa.Integer(), nullable=True),
        sa.Column("source_column", sa.Integer(), nullable=True),
        sa.Column("inventory_version_id", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(16), primary_key=True, nullable=False, server_default="A"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    bind.execute(sa.text(
        "INSERT INTO inventory_items (sku, price, set_type, source_sheet, source_row, source_column, inventory_version_id, category, updated_at) "
        "SELECT sku, price, set_type, source_sheet, source_row, source_column, inventory_version_id, COALESCE(category, 'A'), updated_at FROM inventory_items_legacy_category"
    ))
    op.drop_table("inventory_items_legacy_category")


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("inventory_items"):
        return
    pk = inspector.get_pk_constraint("inventory_items").get("constrained_columns") or []
    if pk == ["sku"]:
        return
    bind = op.get_bind()
    op.rename_table("inventory_items", "inventory_items_new_category")
    op.create_table(
        "inventory_items",
        sa.Column("sku", sa.String(255), primary_key=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("set_type", sa.String(64), nullable=True),
        sa.Column("source_sheet", sa.String(255), nullable=True),
        sa.Column("source_row", sa.Integer(), nullable=True),
        sa.Column("source_column", sa.Integer(), nullable=True),
        sa.Column("inventory_version_id", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(16), nullable=False, server_default="A"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    bind.execute(sa.text(
        "INSERT INTO inventory_items (sku, price, set_type, source_sheet, source_row, source_column, inventory_version_id, category, updated_at) "
        "SELECT sku, price, set_type, source_sheet, source_row, source_column, inventory_version_id, category, updated_at FROM inventory_items_new_category"
    ))
    op.drop_table("inventory_items_new_category")
