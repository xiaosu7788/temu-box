from openpyxl import Workbook
from fastapi.testclient import TestClient

from app.main import app
from app.services.inventory import build_price_catalog


def test_price_header_can_appear_in_middle(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "库存0"
    sheet.cell(30, 12, "价格")
    sheet.cell(31, 8, "MB131-TEST1")
    sheet.cell(31, 9, 99)
    sheet.cell(31, 12, 17.1)
    path = tmp_path / "header.xlsx"
    workbook.save(path)

    catalog = build_price_catalog(path)

    assert catalog["MB131-TEST1"]["price"] == 17.1
    assert catalog["MB131-TEST1"]["source_column"] == 12


def test_price_search_expands_around_sku(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "库存0"
    sheet.cell(1, 8, "MB131-TEST2")
    sheet.cell(1, 9, "六件套")
    sheet.cell(1, 10, 18.2)
    path = tmp_path / "sides.xlsx"
    workbook.save(path)

    catalog = build_price_catalog(path)

    assert catalog["MB131-TEST2"]["price"] == 18.2
    assert catalog["MB131-TEST2"]["set_type"] == "6件套"


def test_duplicate_sku_uses_lowest_price(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "库存0"
    sheet.cell(1, 8, "MB131-TEST3")
    sheet.cell(1, 9, 20)
    sheet.cell(2, 8, "MB131-TEST3")
    sheet.cell(2, 9, 15)
    path = tmp_path / "duplicates.xlsx"
    workbook.save(path)

    catalog = build_price_catalog(path)

    assert catalog["MB131-TEST3"]["price"] == 15


def test_inventory_items_endpoint_filters_and_paginates(monkeypatch):
    monkeypatch.setattr(
        "app.main.load_price_catalog",
        lambda: {
            "MB131-A": {"sku": "MB131-A", "price": 17.1, "set_type": "单品"},
            "MB131-B": {"sku": "MB131-B", "price": 22.0, "set_type": "2件套"},
        },
    )

    response = TestClient(app).get(
        "/api/inventory/items",
        params={"query": "mb131-b", "page": 1, "page_size": 10},
    )

    assert response.status_code == 200
    assert response.json() == {
        "total": 1,
        "items": [{"sku": "MB131-B", "price": 22.0, "set_type": "2件套"}],
    }
