from openpyxl import Workbook

from app.database import (
    delete_inventory_item,
    get_inventory_catalog,
    load_half_entries,
    delete_half_entry,
    save_inventory_catalog,
    merge_half_entries,
)


def test_inventory_item_deletion_is_persisted():
    signature = {"path": "test-inventory.xlsx", "size": 1, "mtime_ns": 1, "parser_version": 3}
    item = {"sku": "MB131-DELETE-01", "price": 17.1, "set_type": "单品"}
    save_inventory_catalog(signature, {item["sku"]: item})

    assert delete_inventory_item(item["sku"]) is True
    assert item["sku"] not in get_inventory_catalog()

    # A fresh rebuild represents a new source table and clears old exclusions.
    save_inventory_catalog(signature, {item["sku"]: item})
    assert item["sku"] in get_inventory_catalog()
from app.services.inventory import inventory_signature


def test_database_catalog_round_trip(tmp_path):
    catalog = {"MB131-DB": {"sku": "MB131-DB", "price": 17.1, "set_type": "单品", "source_sheet": "库存0", "source_row": 2, "source_column": 9}}
    source = tmp_path / "inventory.xlsx"
    workbook = Workbook()
    workbook.save(source)
    save_inventory_catalog(inventory_signature(source), catalog)

    assert get_inventory_catalog()["MB131-DB"]["price"] == 17.1


def test_half_headcost_database_round_trip():
    from app.services.categories import default_category_id

    category_id = default_category_id()
    added, total = merge_half_entries(category_id, {"MB131-HALF": "6件套"})
    assert added == 1
    assert total >= 1
    assert load_half_entries(category_id)["MB131-HALF"] == "6件套"
    assert delete_half_entry(category_id, "MB131-HALF") is True


def test_ensure_category_schema_upgrades_legacy_sqlite(tmp_path, monkeypatch):
    """旧库（无品类维度）升级后数据全部归入默认品类，且迁移可重复执行。"""
    from sqlalchemy import create_engine, text

    import app.database as db

    legacy_db = tmp_path / "legacy.db"
    legacy_url = f"sqlite:///{legacy_db.as_posix()}"
    legacy = create_engine(legacy_url)
    with legacy.begin() as conn:
        # 旧 region_configs：无 category_id，且带旧索引（曾导致重建时索引名冲突）
        conn.execute(text(
            "CREATE TABLE region_configs (id INTEGER PRIMARY KEY AUTOINCREMENT, region_id INTEGER, "
            "module VARCHAR(20), strategy VARCHAR(64), config_json TEXT, version INTEGER, "
            "updated_by INTEGER, updated_at DATETIME)"
        ))
        conn.execute(text("CREATE INDEX ix_region_configs_region_id ON region_configs (region_id)"))
        conn.execute(text(
            "INSERT INTO region_configs (region_id, module, strategy, config_json, version, updated_at) "
            "VALUES (1, 'order', 'standard_order_v1', '{}', 3, '2026-01-01 00:00:00')"
        ))
        # 旧 half_headcost_skus：sku 单列主键
        conn.execute(text(
            "CREATE TABLE half_headcost_skus (sku VARCHAR(255) PRIMARY KEY, set_type VARCHAR(64), updated_at DATETIME)"
        ))
        conn.execute(text("INSERT INTO half_headcost_skus (sku, set_type) VALUES ('LEGACY-SKU', '单品')"))
        # 旧 activity_jobs：无 category_code / category_name
        conn.execute(text(
            "CREATE TABLE activity_jobs (id VARCHAR(64) PRIMARY KEY, owner_id INTEGER, status VARCHAR(32), "
            "filename VARCHAR(255), output_path VARCHAR(1024), stats TEXT, progress INTEGER, message VARCHAR(255), "
            "logs TEXT, region_code VARCHAR(16), region_name VARCHAR(80), config_version INTEGER, "
            "config_snapshot TEXT, created_at DATETIME)"
        ))
        conn.execute(text(
            "INSERT INTO activity_jobs (id, status, filename, stats, region_code, region_name, config_version, config_snapshot) "
            "VALUES ('legacy-job', 'completed', 'legacy.xlsx', '{}', 'US', '美国区', 2, '{}')"
        ))
    legacy.dispose()

    monkeypatch.setattr(db, "engine", create_engine(legacy_url))
    db._ensure_category_schema()
    db._ensure_category_schema()  # 幂等：二次执行不报错

    with db.engine.begin() as conn:
        category = conn.execute(text("SELECT id, code FROM categories WHERE is_default")).first()
        assert category is not None and category[1] == "A"
        region = conn.execute(text("SELECT region_id, category_id, version FROM region_configs")).first()
        assert region == (1, category[0], 3)
        half = conn.execute(text("SELECT category_id, sku, set_type FROM half_headcost_skus")).first()
        assert half == (category[0], "LEGACY-SKU", "单品")
        job = conn.execute(text("SELECT category_code, category_name, filename FROM activity_jobs")).first()
        assert job == ("A", "A品类", "legacy.xlsx")
    db.engine.dispose()
