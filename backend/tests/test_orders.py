from app.services.orders import calc_order_cost


def test_order_cost_uses_half_headcost_and_fees():
    items = [(17.1, "6件套", 2, "MB131-491")]

    result = calc_order_cost(items, {"MB131-491": "6件套"})

    assert result == 48.2


def test_order_cost_is_none_when_price_missing():
    assert calc_order_cost([(None, "单品", 1, "MB131-X")]) is None


def test_order_cost_uses_admin_settings():
    settings = {"order": {"headcost": {"6件套": 8}, "operation_fee": 9, "extra_item_fee": 3}}
    assert calc_order_cost([(17.1, "6件套", 1, "MB131-491")], settings=settings) == 34.1


def test_order_cost_adds_tail_fee_and_subtracts_shipping_subsidy():
    settings = {
        "order": {
            "headcost": {"单品": 5},
            "operation_fee": 7,
            "extra_item_fee": 2,
            "tail_fee": 4,
            "shipping_subsidy": 1.5,
        }
    }
    assert calc_order_cost([(10, "单品", 2, "MB131-TAIL")], settings=settings) == 41.5


def test_manual_combo_cost_has_no_headcost_and_no_order_fees():
    items = [(55.94, "12件套", 1, "MB131-666")]
    # 手动组合：货值已含全部费用 → 成本就是库存货值
    assert calc_order_cost(items, None, None, {"MB131-666"}) == 55.94
    # 多件：件数 × 货值，不加多件费
    assert calc_order_cost([(55.94, "12件套", 2, "MB131-666")], None, None, {"MB131-666"}) == 111.88
    # 未标手动组合时：货值 + 12件套头程 15 + 操作费 7
    assert calc_order_cost(items, None, None) == 77.94
    # 混合单：只有非手动组合部分承担操作费（15 + 55.94 + 7）
    assert calc_order_cost([(10, "单品", 1, "A"), (55.94, "12件套", 1, "MB131-666")], None, None, {"MB131-666"}) == 77.94
    # 混合单多件：多件费只按非手动组合件数算（15 + 55.94×2 + 7）
    assert calc_order_cost([(10, "单品", 1, "A"), (55.94, "12件套", 2, "MB131-666")], None, None, {"MB131-666"}) == 133.88
