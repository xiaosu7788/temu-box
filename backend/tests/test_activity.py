import json
from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from app.database import create_user
from app.main import app
from app.services.activity import activity_base_price, match_id_profit_rule, normalize_id_profit_rules, parse_skc, preview_activity_workbook, process_activity_workbook
from app.services.auth import hash_password


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


def test_parse_custom_activity_skc_rules():
    common = {
        "set_keywords": ["piece", "件套", "套装"],
        "set_mappings": [{"pattern": "四件组合", "pieces": 4}],
        "single_mode": "first_segment",
        "single_delimiter": "-",
        "single_marker": "price",
    }
    assert parse_skc("y1-4piece", common) == ("set", 4.0)
    assert parse_skc("ABC-8件套", common) == ("set", 8.0)
    assert parse_skc("春季四件组合-A", common) == ("set", 4.0)
    assert parse_skc("5-MB131-A", common) == ("single", 5.0)

    last_segment = {**common, "single_mode": "last_segment"}
    assert parse_skc("MB131-A-5.5", last_segment) == ("single", 5.5)

    after_marker = {**common, "single_mode": "after_marker"}
    assert parse_skc("MB131-price17.1", after_marker) == ("single", 17.1)

    empty_set_marker = {**common, "set_keywords": [""]}
    assert parse_skc("SET-A-10", empty_set_marker) == ("set", 10.0)


def test_no_set_category_rejects_set_skus_and_custom_set_rules():
    """无套装品类：套装 SKC 不识别；自定义规则带套装关键字在提交时被拒。"""
    import pytest

    from app.services.activity import normalize_parse_config, settings_allowed_pieces
    from app.services.categories import category_defaults

    settings = category_defaults("no_set")
    assert settings["activity"]["set_prices"] == {}
    assert settings_allowed_pieces(settings) == frozenset()

    config = normalize_parse_config(settings["activity"]["default_skc_rules"], allowed_pieces=settings_allowed_pieces(settings))
    assert parse_skc("ABC-4件套", config, normalized=True) is None
    assert parse_skc("y1-8piece", config, normalized=True) is None
    assert parse_skc("MB131-A-5", config, normalized=True) == ("single", 5.0)

    with pytest.raises(ValueError, match="无套装档位"):
        normalize_parse_config({"set_keywords": ["件套"], "set_mappings": [], "single_mode": "last_segment", "single_delimiter": "-", "single_marker": "price"}, allowed_pieces=frozenset())

    # 旧快照（无 set_prices 键）仍回落系统档位
    assert settings_allowed_pieces({"activity": {"headcost": 5}}) == frozenset({4, 5, 6, 8, 10, 12})


def test_activity_price_uses_admin_settings():
    settings = {"activity": {"headcost": 6, "operation_fee": 8, "set_prices": {"4": 50}, "single_tiers": [{"min_price": 0, "profit": 0}, {"min_price": 15, "profit": 4}]}}
    assert activity_base_price(("single", 15.0), settings) == 33.0
    assert activity_base_price(("set", 4.0), settings) == 50.0


def test_id_profit_rules_match_in_spu_skc_sku_priority_order():
    rules = normalize_id_profit_rules([
        {"id_type": "SPU", "id": "spu-1", "profit": 5},
        {"id_type": "SKC", "id": "skc-1", "profit": 10},
        {"id_type": "SKU", "id": "sku-1", "profit": -10},
    ])
    matched = match_id_profit_rule({"SPU": "SPU-1", "SKC": "SKC-1", "SKU": "SKU-1"}, rules)
    assert matched == {"id_type": "SPU", "id": "spu-1", "profit": 5.0, "matched_id": "SPU-1"}


def test_id_profit_rules_adjust_activity_price_and_reference_boundaries(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["SPU ID", "SKC ID", "SKU ID", "SKC货号", "活动申报价格"])
    sheet.append(["spu-low", "skc-low", "sku-low", "y1-4piece", 61])
    sheet.append(["spu-equal", "skc-equal", "sku-equal", "y1-4piece", 62])
    sheet.append(["spu-high", "skc-high", "sku-high", "y1-4piece", 63])
    content = BytesIO()
    workbook.save(content)
    workbook.close()

    output = tmp_path / "id-rules.xlsx"
    stats = process_activity_workbook(
        content.getvalue(),
        output,
        id_profit_rules=[{"id_type": "SPU", "id": "spu-low", "profit": 20},
                         {"id_type": "SPU", "id": "spu-equal", "profit": 20},
                         {"id_type": "SPU", "id": "spu-high", "profit": 20}],
    )

    assert stats["id_profit_rule_matches"] == 3
    assert stats["removed_rows"] == 1
    assert stats["unchanged_rows"] == 1
    assert stats["updated_rows"] == 1
    result = load_workbook(output, data_only=True)
    rows = [(result.active.cell(row, 1).value, result.active.cell(row, 5).value) for row in range(2, result.active.max_row + 1)]
    result.close()
    assert rows[0] == ("spu-equal", 62)
    assert rows[1][0] == "spu-high"
    assert 62 < rows[1][1] <= 63


def test_id_profit_rules_do_not_match_when_id_columns_are_missing(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["SKC货号", "活动申报价格"])
    sheet.append(["MB131-A-5", 20])
    content = BytesIO()
    workbook.save(content)
    workbook.close()

    output = tmp_path / "missing-id-columns.xlsx"
    stats = process_activity_workbook(
        content.getvalue(),
        output,
        id_profit_rules=[{"id_type": "SKU", "id": "sku-1", "profit": 100}],
    )
    assert stats["id_profit_rule_matches"] == 0
    assert stats["updated_rows"] == 1


def test_empty_custom_id_profit_rules_override_default_rules(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["SPU ID", "SKC货号", "活动申报价格"])
    sheet.append(["spu-1", "y1-4piece", 40])
    content = BytesIO()
    workbook.save(content)
    workbook.close()

    output = tmp_path / "empty-custom-rules.xlsx"
    settings = {"activity": {"id_profit_rules": [{"id_type": "SPU", "id": "spu-1", "profit": 20}]}}
    stats = process_activity_workbook(content.getvalue(), output, settings, id_profit_rules=[])
    assert stats["id_profit_rule_matches"] == 0
    assert stats["removed_rows"] == 1


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


def test_activity_uses_default_skc_rules_from_settings(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["SKC货号", "活动申报价格"])
    sheet.append(["MB131-price17.1", 40])
    content = BytesIO()
    workbook.save(content)
    workbook.close()
    settings = {
        "activity": {
            "default_skc_rules": {
                "set_keywords": ["piece"],
                "set_mappings": [],
                "single_mode": "after_marker",
                "single_delimiter": "-",
                "single_marker": "price",
            }
        }
    }
    output = tmp_path / "default-rules.xlsx"

    stats = process_activity_workbook(content.getvalue(), output, settings)

    assert stats["processed_rows"] == 1
    assert stats["custom_skc_rules"] is False

def test_activity_uplift_limit_uses_configured_value(tmp_path):
    output = tmp_path / "limited-result.xlsx"
    settings = {"activity": {"uplift_limit": 0.25}}
    stats = process_activity_workbook(make_activity_workbook(), output, settings)

    workbook = load_workbook(output, data_only=True)
    sheet = workbook["活动申报价格"]
    rows = [(sheet.cell(row, 4).value, sheet.cell(row, 6).value) for row in range(2, sheet.max_row + 1)]
    workbook.close()

    assert stats["uplift_limit"] == 0.25
    assert rows[1][0] == "MB131-B-5"
    assert 17 < rows[1][1] <= 17.25


def test_preview_custom_activity_rules():
    rules = {
        "set_keywords": ["piece", "件套"],
        "set_mappings": [],
        "single_mode": "first_segment",
        "single_delimiter": "-",
        "single_marker": "price",
    }
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["SKC货号", "活动申报价格"])
    sheet.append(["5-MB131-A", 20])
    sheet.append(["ABC-8件套", 80])
    sheet.append(["UNKNOWN", 20])
    content = BytesIO()
    workbook.save(content)
    workbook.close()

    preview = preview_activity_workbook(content.getvalue(), rules)
    assert preview["total_rows"] == 3
    assert preview["single_rows"] == 1
    assert preview["set_rows"] == 1
    assert preview["unrecognized_rows"] == 1
    assert preview["items"][0]["base_price"] == 17


def test_activity_preview_endpoint_uses_custom_rules():
    create_user("activity_preview_user", hash_password("previewpass123"), status="approved")
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["SKC货号", "活动申报价格"])
    sheet.append(["MB131-price17.1", 30])
    content = BytesIO()
    workbook.save(content)
    workbook.close()
    rules = {
        "set_keywords": ["piece"],
        "set_mappings": [],
        "single_mode": "after_marker",
        "single_delimiter": "-",
        "single_marker": "price",
    }

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "activity_preview_user", "password": "previewpass123"})
        response = client.post(
            "/api/activities/preview",
            data={"skc_rules": json.dumps(rules, ensure_ascii=False)},
            files={"file": ("preview.xlsx", content.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 200
        assert response.json()["items"][0]["result"] == "单品"
        assert response.json()["items"][0]["value"] == 17.1


def test_activity_task_is_visible_after_submission():
    create_user("activity_owner", hash_password("activitypass123"), status="approved")
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["SKC货号", "活动申报价格"])
    sheet.append(["MB131-A-5", 17])
    content = BytesIO()
    workbook.save(content)
    workbook.close()

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "activity_owner", "password": "activitypass123"})
        assert login.status_code == 200
        response = client.post(
            "/api/activities/bulk",
            files={"file": ("activity.xlsx", content.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 202
        job_id = response.json()["id"]
        listed = client.get("/api/activities")
        assert listed.status_code == 200
        assert job_id in {item["id"] for item in listed.json()["items"]}


def test_custom_uplift_does_not_change_default_settings():
    create_user("custom_uplift_user", hash_password("custompass123"), status="approved")
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["SKC货号", "活动申报价格"])
    sheet.append(["MB131-CUSTOM-5", 18])
    content = BytesIO()
    workbook.save(content)
    workbook.close()

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "custom_uplift_user", "password": "custompass123"})
        default_before = client.get("/api/settings").json()["activity"]["uplift_limit"]
        response = client.post(
            "/api/activities/bulk",
            data={"uplift_limit": "0.25"},
            files={"file": ("custom-uplift.xlsx", content.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 202
        assert client.get("/api/settings").json()["activity"]["uplift_limit"] == default_before


def test_admin_frontend_activity_list_is_scoped_but_admin_list_includes_all_jobs():
    create_user("activity_admin", hash_password("activityadmin123"), role="admin", status="approved")
    create_user("activity_user", hash_password("activityuser123"), status="approved")
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["SKC货号", "活动申报价格"])
    sheet.append(["MB131-ADMIN-5", 17])
    content = BytesIO()
    workbook.save(content)
    workbook.close()

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "activity_user", "password": "activityuser123"})
        response = client.post(
            "/api/activities/bulk",
            files={"file": ("activity-user.xlsx", content.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 202
        job_id = response.json()["id"]

        client.post("/api/auth/logout")
        client.post("/api/auth/login", json={"username": "activity_admin", "password": "activityadmin123"})
        frontend_ids = {item["id"] for item in client.get("/api/activities").json()["items"]}
        admin_ids = {item["id"] for item in client.get("/api/admin/activity-tasks").json()["items"]}
        assert job_id not in frontend_ids
        assert job_id in admin_ids
        assert client.get(f"/api/activities/{job_id}").status_code == 404
        assert client.delete(f"/api/activities/{job_id}").status_code == 404


def make_paged_activity_workbook(rows: int = 12) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "活动申报价格"
    sheet.append(["SKC货号", "活动申报价格"])
    for index in range(rows):
        # 5 号单品基础活动价 17；申报价 20 触发上浮，申报价 16 触发删除
        skc = f"MB{index:03d}-A-5"
        price = 20 if index % 3 == 0 else 16 if index % 3 == 1 else 17
        sheet.append([skc, price])
    content = BytesIO()
    workbook.save(content)
    workbook.close()
    return content.getvalue()


def test_preview_pagination_returns_every_row_once():
    content = make_paged_activity_workbook(12)
    seen_rows = []
    for page in range(1, 4):
        preview = preview_activity_workbook(content, page=page, page_size=5)
        assert preview["total_items"] == 12
        assert preview["total_pages"] == 3
        assert preview["page"] == page
        assert preview["page_size"] == 5
        seen_rows.extend(item["row"] for item in preview["items"])

    assert seen_rows == list(range(2, 14))


def test_preview_page_is_clamped_and_size_is_capped():
    content = make_paged_activity_workbook(3)
    beyond = preview_activity_workbook(content, page=99, page_size=1000)
    assert beyond["page"] == 1
    assert beyond["page_size"] == 500
    assert len(beyond["items"]) == 3


def test_preview_result_filter_only_affects_details():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["SKC货号", "活动申报价格"])
    sheet.append(["MB131-A-5", 20])
    sheet.append(["y1-4piece", 60])
    sheet.append(["UNKNOWN", 20])
    content = BytesIO()
    workbook.save(content)
    workbook.close()

    preview = preview_activity_workbook(content.getvalue(), result_filter="套装")

    assert preview["result_filter"] == "套装"
    assert preview["total_rows"] == 3
    assert preview["single_rows"] == 1
    assert preview["set_rows"] == 1
    assert preview["unrecognized_rows"] == 1
    assert preview["total_items"] == 1
    assert [item["result"] for item in preview["items"]] == ["套装"]


def test_preview_reports_final_price_range_and_actions():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["SKC货号", "活动申报价格"])
    sheet.append(["MB131-A-5", 20])   # 基础 17，申报 20 → 上浮到 (17, 18]
    sheet.append(["MB131-B-5", 17])   # 基础 17，申报 17 → 不变
    sheet.append(["MB131-C-5", 16])   # 申报低于基础 → 将被删除
    content = BytesIO()
    workbook.save(content)
    workbook.close()

    preview = preview_activity_workbook(content.getvalue(), settings={"activity": {"uplift_limit": 1}})
    items = {item["skc"]: item for item in preview["items"]}

    assert preview["uplift_limit"] == 1
    uplift = items["MB131-A-5"]
    assert uplift["base_price"] == 17
    assert uplift["reference_price"] == 20
    assert uplift["final_price_low"] == 17
    assert uplift["final_price_high"] == 18
    assert uplift["action"] == "上浮"

    unchanged = items["MB131-B-5"]
    assert unchanged["action"] == "不变"
    assert unchanged["final_price_low"] == unchanged["final_price_high"] == 17

    removed = items["MB131-C-5"]
    assert removed["action"] == "将被删除"
    assert removed["final_price_low"] is None and removed["final_price_high"] is None


def test_preview_final_price_high_is_capped_by_reference_price():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["SKC货号", "活动申报价格"])
    sheet.append(["MB131-A-5", 17.4])  # 浮动上限 1，但申报价只允许上浮 0.4
    content = BytesIO()
    workbook.save(content)
    workbook.close()

    preview = preview_activity_workbook(content.getvalue(), settings={"activity": {"uplift_limit": 1}})

    assert preview["items"][0]["final_price_high"] == 17.4


def test_preview_includes_id_profit_rule_in_base_price():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["SPU ID", "SKC货号", "活动申报价格"])
    sheet.append(["9001", "MB131-A-5", 30])
    content = BytesIO()
    workbook.save(content)
    workbook.close()

    rules = [{"id_type": "SPU", "id": "9001", "profit": 2}]
    preview = preview_activity_workbook(content.getvalue(), settings={"activity": {"uplift_limit": 1}}, id_profit_rules=rules)

    item = preview["items"][0]
    assert item["profit_adjustment"] == 2
    assert item["base_price"] == 19
    assert item["final_price_low"] == 19
    assert item["final_price_high"] == 20


def test_activity_preview_endpoint_paginates_and_rejects_bad_filter():
    create_user("activity_page_user", hash_password("pagepass123"), status="approved")
    content = make_paged_activity_workbook(12)

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "activity_page_user", "password": "pagepass123"})
        response = client.post(
            "/api/activities/preview",
            data={"page": "2", "page_size": "5"},
            files={"file": ("preview.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["page"] == 2
        assert payload["total_pages"] == 3
        assert payload["total_items"] == 12
        assert len(payload["items"]) == 5
        assert payload["items"][0]["row"] == 7

        filtered = client.post(
            "/api/activities/preview",
            data={"result_filter": "无法识别"},
            files={"file": ("preview.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert filtered.status_code == 200
        assert filtered.json()["total_items"] == 0
        assert filtered.json()["total_rows"] == 12

        bad = client.post(
            "/api/activities/preview",
            data={"result_filter": "乱填"},
            files={"file": ("preview.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert bad.status_code == 400
