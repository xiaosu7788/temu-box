from openpyxl import Workbook

from app.database import (
    get_inventory_catalog,
    load_half_entries,
    delete_half_entry,
    save_inventory_catalog,
    merge_half_entries,
)
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
