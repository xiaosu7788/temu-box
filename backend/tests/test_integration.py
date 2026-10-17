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
