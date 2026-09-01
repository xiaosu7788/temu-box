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
    added, total = merge_half_entries({"MB131-HALF": "6件套"})
    assert added == 1
    assert total >= 1
    assert load_half_entries()["MB131-HALF"] == "6件套"
    assert delete_half_entry("MB131-HALF") is True
