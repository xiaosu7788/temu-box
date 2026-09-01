from io import BytesIO

from openpyxl import Workbook, load_workbook

from app.services.activity import activity_base_price, parse_skc, process_activity_workbook


def make_activity_workbook() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "活动申报价格"
    sheet.append(["活动类型(活动主题）", "SPU ID", "SKC ID", "SKC货号", "SKU ID", "活动申报价格"])
    sheet.append(["活动", "1", "1", "MB131-A-5", "10", 17])
    sheet.append(["活动", "2", "2", "MB131-B-5", "11", 18])
    sheet.append(["活动", "3", "3", "MB131-C-5", "12", 16])
    sheet.append(["活动", "4", "4", "y1-4piece", "13", 42])
    sheet.append(["活动", "5", "5", "y1-5piece", "14", 45.5])
    sheet.append(["活动", "6", "6", "y1-6piece", "15", 47])

    inventory = workbook.create_sheet("活动库存")
    inventory.append(["活动类型(活动主题）", "SPU ID", "活动库存"])
    inventory.append(["活动", "1", 15])

    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def test_parse_activity_skc_rules():
    assert parse_skc("MB131-A-5") == ("single", 5.0)
    assert parse_skc("y1-8piece") == ("set", 8.0)
    assert activity_base_price(("single", 5.0)) == 17.0
    assert activity_base_price(("set", 12.0)) == 92.0


def test_process_activity_workbook_updates_filters_and_preserves_sheets(tmp_path):
    output = tmp_path / "result.xlsx"
    stats = process_activity_workbook(make_activity_workbook(), output)

    assert stats["input_data_rows"] == 6
    assert stats["processed_rows"] == 6
    assert stats["unchanged_rows"] == 2
    assert stats["removed_rows"] == 2
    assert stats["updated_rows"] == 2
    assert stats["remaining_data_rows"] == 4

    workbook = load_workbook(output, data_only=True)
    assert workbook.sheetnames == ["活动申报价格", "活动库存"]
    sheet = workbook["活动申报价格"]
    rows = [(sheet.cell(row, 4).value, sheet.cell(row, 6).value) for row in range(2, sheet.max_row + 1)]
    workbook.close()

    assert rows[0] == ("MB131-A-5", 17)
    assert rows[1][0] == "MB131-B-5"
    assert 17 < rows[1][1] <= 18
    assert rows[2] == ("y1-4piece", 42)
    assert rows[3][0] == "y1-5piece"
    assert 45 < rows[3][1] <= 45.5
