from openpyxl import Workbook, load_workbook

from app.services.orders import build_delivery_sku_map, generate_summary


def test_delivery_to_summary_pipeline(tmp_path):
    delivery = Workbook()
    delivery_ws = delivery.active
    delivery_ws.append(["", "参考单号"] + [""] * 17 + ["SKU", "数量"])
    delivery_ws.append(["", "PO-TEST-001"] + [""] * 17 + ["MB131-491", 2])
    delivery_path = tmp_path / "delivery.xlsx"
    delivery.save(delivery_path)

    sales = Workbook()
    sales_ws = sales.active
    sales_ws.append(["", "", "PO单号", "", "", "结算金额"])
    sales_ws.append(["", "", "PO-TEST-001", "", "", 88.5])
    sales_path = tmp_path / "sales.xlsx"
    sales.save(sales_path)

    output_path = tmp_path / "summary.xlsx"
    po_map = build_delivery_sku_map(delivery_path)
    stats = generate_summary(
        sales_path,
        {"MB131-491": {"price": 17.1, "set_type": "6件套"}},
        po_map,
        output_path,
        {"MB131-491": "6件套"},
    )

    assert stats["matched"] == 1
    assert stats["unmatched"] == 0
    workbook = load_workbook(output_path, data_only=True)
    row = [workbook.active.cell(2, column).value for column in range(1, 8)]
    workbook.close()
    assert row == ["PO-TEST-001", "MB131-491", 17.1, 2, 48.2, 88.5, "6件套"]


def test_summary_marks_manual_combo_value_as_slash(tmp_path):
    delivery = Workbook()
    delivery_ws = delivery.active
    delivery_ws.append(["", "参考单号"] + [""] * 17 + ["SKU", "数量"])
    delivery_ws.append(["", "PO-TEST-002"] + [""] * 17 + ["MB131-666", 1])
    delivery_path = tmp_path / "delivery2.xlsx"
    delivery.save(delivery_path)

    sales = Workbook()
    sales_ws = sales.active
    sales_ws.append(["", "", "PO单号", "", "", "结算金额"])
    sales_ws.append(["", "", "PO-TEST-002", "", "", 88.5])
    sales_path = tmp_path / "sales2.xlsx"
    sales.save(sales_path)

    output_path = tmp_path / "summary2.xlsx"
    generate_summary(
        sales_path,
        {"MB131-666": {"price": 55.94, "set_type": "12件套", "manual_combo": True}},
        build_delivery_sku_map(delivery_path),
        output_path,
    )

    workbook = load_workbook(output_path, data_only=True)
    row = [workbook.active.cell(2, column).value for column in range(1, 8)]
    workbook.close()
    # 货值列显示 /，成本就是库存货值（不加头程、也不加操作费）
    assert row == ["PO-TEST-002", "MB131-666", "/", 1, 55.94, 88.5, "12件套"]


def test_summary_merges_same_po_rows_and_keeps_inner_borders_clean(tmp_path):
    """同一 PO 的多行必须合并成一个视觉块：合并区域内部不能再有横线。"""
    delivery = Workbook()
    delivery_ws = delivery.active
    delivery_ws.append(["", "参考单号"] + [""] * 17 + ["SKU", "数量"])
    delivery_ws.append(["", "PO-MERGE-001"] + [""] * 17 + ["MB131-253", 1])
    delivery_path = tmp_path / "delivery_merge.xlsx"
    delivery.save(delivery_path)

    sales = Workbook()
    sales_ws = sales.active
    sales_ws.append(["", "", "PO单号", "", "", "结算金额"])
    for amount in (20.11, 18.16, 22.06, 17.85, 75.04):
        sales_ws.append(["", "", "PO-MERGE-001", "", "", amount])
    sales_path = tmp_path / "sales_merge.xlsx"
    sales.save(sales_path)

    output_path = tmp_path / "summary_merge.xlsx"
    generate_summary(
        sales_path,
        {"MB131-253": {"price": 8, "set_type": "单品"}},
        build_delivery_sku_map(delivery_path),
        output_path,
    )

    workbook = load_workbook(output_path)
    sheet = workbook.active
    merged = {str(item) for item in sheet.merged_cells.ranges}
    # 5 行金额 → 合并 A2:A6 等 6 列；结算金额 F 列不合并
    assert merged == {"A2:A6", "B2:B6", "C2:C6", "D2:D6", "E2:E6", "G2:G6"}

    # 中间行不能有横向边线，否则 WPS/Excel 看起来像没合并
    for row in range(3, 6):
        for column in (1, 2, 3, 4, 5, 7):
            border = sheet.cell(row, column).border
            assert border.top.style is None, f"{sheet.cell(row, column).coordinate} 不应有上边框"
            assert border.bottom.style is None, f"{sheet.cell(row, column).coordinate} 不应有下边框"
    # 合并块首尾保留外框线
    assert sheet.cell(2, 1).border.top.style == "thin"
    assert sheet.cell(6, 1).border.bottom.style == "thin"
    # 结算金额列每行都有完整边框
    for row in range(2, 7):
        assert sheet.cell(row, 6).border.top.style == "thin"
        assert sheet.cell(row, 6).border.bottom.style == "thin"
    assert [sheet.cell(row, 6).value for row in range(2, 7)] == [20.11, 18.16, 22.06, 17.85, 75.04]
    workbook.close()
