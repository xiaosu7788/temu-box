"""Add category dimension: categories table, region_configs/half_headcost_skus
category scoping, and activity job category columns.

存量数据全部归入默认 A 品类（套装型），与迁移前行为一致。
"""
from alembic import op
import sqlalchemy as sa

revision = "20260908_01"
down_revision = "20260903_01"
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set:
    if not inspector.has_table(table):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("categories"):
        op.create_table(
            "categories",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("code", sa.String(16), nullable=False),
            sa.Column("name", sa.String(80), nullable=False),
            sa.Column("template_type", sa.String(20), nullable=False, server_default="set_based"),
            sa.Column("set_types", sa.Text(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("code", name="uq_categories_code"),
        )
        op.create_index("ix_categories_code", "categories", ["code"], unique=True)
    if not bind.execute(sa.text("SELECT 1 FROM categories LIMIT 1")).scalar():
        bind.execute(sa.text(
            "INSERT INTO categories (code, name, template_type, set_types, enabled, is_default, sort_order) "
            "VALUES ('A', 'A品类', 'set_based', '[]', true, true, 10)"
        ))
    category_id = bind.execute(sa.text("SELECT id FROM categories WHERE is_default LIMIT 1")).scalar()

    if "category_id" not in _columns(inspector, "region_configs"):
        op.rename_table("region_configs", "region_configs_legacy")
        op.create_table(
            "region_configs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("region_id", sa.Integer(), nullable=False),
            sa.Column("category_id", sa.Integer(), nullable=False),
            sa.Column("module", sa.String(20), nullable=False),
            sa.Column("strategy", sa.String(64), nullable=False),
            sa.Column("config_json", sa.Text(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("updated_by", sa.Integer(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("region_id", "category_id", "module", name="uq_region_config_category_module"),
        )
        op.create_index("ix_region_configs_region_id", "region_configs", ["region_id"])
        op.create_index("ix_region_configs_category_id", "region_configs", ["category_id"])
        bind.execute(sa.text(
            "INSERT INTO region_configs (region_id, category_id, module, strategy, config_json, version, updated_by, updated_at) "
            "SELECT region_id, :cid, module, strategy, config_json, version, updated_by, updated_at FROM region_configs_legacy"
        ), {"cid": category_id})
        op.drop_table("region_configs_legacy")
    else:
        bind.execute(sa.text("UPDATE region_configs SET category_id = :cid WHERE category_id IS NULL"), {"cid": category_id})

    if "category_id" not in _columns(inspector, "half_headcost_skus"):
        op.rename_table("half_headcost_skus", "half_headcost_skus_legacy")
        op.create_table(
            "half_headcost_skus",
            sa.Column("category_id", sa.Integer(), primary_key=True),
            sa.Column("sku", sa.String(255), primary_key=True),
            sa.Column("set_type", sa.String(64), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
        bind.execute(sa.text(
            "INSERT INTO half_headcost_skus (category_id, sku, set_type, updated_at) "
            "SELECT :cid, sku, COALESCE(set_type, '单品'), COALESCE(updated_at, CURRENT_TIMESTAMP) FROM half_headcost_skus_legacy"
        ), {"cid": category_id})
        op.drop_table("half_headcost_skus_legacy")

    activity_columns = _columns(inspector, "activity_jobs")
    if "category_code" not in activity_columns:
        op.add_column("activity_jobs", sa.Column("category_code", sa.String(16), nullable=False, server_default="A"))
    if "category_name" not in activity_columns:
        op.add_column("activity_jobs", sa.Column("category_name", sa.String(80), nullable=False, server_default="A品类"))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    activity_columns = _columns(inspector, "activity_jobs")
    if "category_name" in activity_columns:
        op.drop_column("activity_jobs", "category_name")
    if "category_code" in activity_columns:
        op.drop_column("activity_jobs", "category_code")
    if "category_id" in _columns(inspector, "half_headcost_skus"):
        op.rename_table("half_headcost_skus", "half_headcost_skus_legacy")
        op.create_table(
            "half_headcost_skus",
            sa.Column("sku", sa.String(255), primary_key=True),
            sa.Column("set_type", sa.String(64), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
        bind.execute(sa.text(
            "INSERT INTO half_headcost_skus (sku, set_type, updated_at) "
            "SELECT sku, set_type, updated_at FROM half_headcost_skus_legacy WHERE category_id = (SELECT id FROM categories WHERE is_default LIMIT 1)"
        ))
        op.drop_table("half_headcost_skus_legacy")
    if "category_id" in _columns(inspector, "region_configs"):
        op.rename_table("region_configs", "region_configs_legacy")
        op.create_table(
            "region_configs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("region_id", sa.Integer(), nullable=False),
            sa.Column("module", sa.String(20), nullable=False),
            sa.Column("strategy", sa.String(64), nullable=False),
            sa.Column("config_json", sa.Text(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("updated_by", sa.Integer(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("region_id", "module", name="uq_region_config_module"),
        )
        op.create_index("ix_region_configs_region_id", "region_configs", ["region_id"])
        bind.execute(sa.text(
            "INSERT INTO region_configs (region_id, module, strategy, config_json, version, updated_by, updated_at) "
            "SELECT region_id, module, strategy, config_json, version, updated_by, updated_at FROM region_configs_legacy "
            "WHERE category_id = (SELECT id FROM categories WHERE is_default LIMIT 1)"
        ))
        op.drop_table("region_configs_legacy")
    if inspector.has_table("categories"):
        op.drop_index("ix_categories_code", table_name="categories")
        op.drop_table("categories")
