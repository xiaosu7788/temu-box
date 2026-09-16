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


def _release_pk_name(inspector, table: str) -> None:
    """PostgreSQL 的主键约束名（{table}_pkey）不随表改名而变化，
    新建同名表时主键会重名冲突，先改名释放。SQLite 为内部命名，无此问题。"""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    pk_name = (inspector.get_pk_constraint(table) or {}).get("name")
    if pk_name:
        op.execute(f'ALTER TABLE {table} RENAME CONSTRAINT "{pk_name}" TO "{table}_legacy_pkey"')


def _release_index_names(inspector, table: str) -> None:
    """删除表上的普通索引，避免表改名后索引名占用导致建新表冲突。"""
    constraint_indexes = {uc.get("name") for uc in inspector.get_unique_constraints(table)}
    for item in inspector.get_indexes(table):
        name = item.get("name")
        if name and name not in constraint_indexes:
            op.drop_index(name, table_name=table)


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
        _release_index_names(inspector, "half_headcost_skus")
        _release_pk_name(inspector, "half_headcost_skus")
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
        _release_index_names(inspector, "half_headcost_skus")
        _release_pk_name(inspector, "half_headcost_skus")
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
