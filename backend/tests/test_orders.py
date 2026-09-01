from app.services.orders import calc_order_cost


def test_order_cost_uses_half_headcost_and_fees():
    items = [(17.1, "6件套", 2, "MB131-491")]

    result = calc_order_cost(items, {"MB131-491": "6件套"})

    assert result == 48.2


def test_order_cost_is_none_when_price_missing():
    assert calc_order_cost([(None, "单品", 1, "MB131-X")]) is None
