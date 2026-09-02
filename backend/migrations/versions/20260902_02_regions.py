"""Add region profiles and task configuration snapshots."""
import json

from alembic import op
import sqlalchemy as sa

revision = "20260902_02"
down_revision = "20260902_01"
branch_labels = None
depends_on = None

ORDER_DEFAULT = {
    "headcost": {"单品": 5, "4件套": 5, "5件套": 5, "6件套": 5, "8件套": 10, "10件套": 10, "12件套": 15},
    "operation_fee": 7, "extra_item_fee": 2, "tail_fee": 0, "shipping_subsidy": 0,
}
ACTIVITY_DEFAULT = {
    "headcost": 5, "operation_fee": 7, "uplift_limit": 1,
    "set_prices": {"4": 42, "5": 45, "6": 48, "8": 71, "10": 75, "12": 92},
    "single_tiers": [{"min_price": 0, "profit": 0}],
    "default_skc_rules": {"set_keywords": ["piece", "件套", "套装"], "set_mappings": [], "single_mode": "last_segment", "single_delimiter": "-", "single_marker": "price"},
}


def _setting(connection, key: str, fallback: dict) -> dict:
    inspector = sa.inspect(connection)
    if not inspector.has_table("app_settings"):
        return fallback
    value = connection.execute(sa.text("SELECT value FROM app_settings WHERE key = :key"), {"key": key}).scalar()
    if not value:
        return fallback
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else fallback
    except json.JSONDecodeError:
        return fallback


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("regions"):
        op.create_table(
            "regions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("code", sa.String(16), nullable=False),
            sa.Column("name", sa.String(80), nullable=False),
            sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("code", name="uq_regions_code"),
        )
        op.create_index("ix_regions_code", "regions", ["code"], unique=True)
    inspector = sa.inspect(bind)
    if not inspector.has_table("region_configs"):
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

    region_id = bind.execute(sa.text("SELECT id FROM regions WHERE code = 'US'")).scalar()
    if region_id is None:
        result = bind.execute(sa.text("INSERT INTO regions (code, name, currency, enabled, is_default, sort_order, created_at, updated_at) VALUES ('US', '美国区', 'CNY', true, true, 10, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
        region_id = result.lastrowid
        if region_id is None:
            region_id = bind.execute(sa.text("SELECT id FROM regions WHERE code = 'US'")).scalar()
    order = _setting(bind, "order", ORDER_DEFAULT)
    activity = _setting(bind, "activity", ACTIVITY_DEFAULT)
    existing = bind.execute(sa.text("SELECT module FROM region_configs WHERE region_id = :id"), {"id": region_id}).scalars().all()
    if "order" not in existing:
        bind.execute(sa.text("INSERT INTO region_configs (region_id, module, strategy, config_json, version, updated_at) VALUES (:id, 'order', 'standard_order_v1', :config, 1, CURRENT_TIMESTAMP)"), {"id": region_id, "config": json.dumps(order, ensure_ascii=False)})
    if "activity" not in existing:
        bind.execute(sa.text("INSERT INTO region_configs (region_id, module, strategy, config_json, version, updated_at) VALUES (:id, 'activity', 'standard_activity_v1', :config, 1, CURRENT_TIMESTAMP)"), {"id": region_id, "config": json.dumps(activity, ensure_ascii=False)})

    inspector = sa.inspect(bind)
    if inspector.has_table("activity_jobs"):
        columns = {column["name"] for column in inspector.get_columns("activity_jobs")}
        additions = {
            "region_code": sa.Column("region_code", sa.String(16), nullable=False, server_default="US"),
            "region_name": sa.Column("region_name", sa.String(80), nullable=False, server_default="美国区"),
            "config_version": sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"),
            "config_snapshot": sa.Column("config_snapshot", sa.Text(), nullable=False, server_default="{}"),
        }
        for name, column in additions.items():
            if name not in columns:
                op.add_column("activity_jobs", column)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("activity_jobs"):
        columns = {column["name"] for column in inspector.get_columns("activity_jobs")}
        for name in ("config_snapshot", "config_version", "region_name", "region_code"):
            if name in columns:
                op.drop_column("activity_jobs", name)
    inspector = sa.inspect(bind)
    if inspector.has_table("region_configs"):
        op.drop_table("region_configs")
    if inspector.has_table("regions"):
        op.drop_table("regions")