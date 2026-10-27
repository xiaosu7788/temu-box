import io

from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.main import app
from app.database import create_user, load_weight_entries
from app.services.auth import hash_password
from app.services.categories import default_category_id
from app.services.orders import calc_order_cost
from app.services.weight_headcost import delete_entry, extract_weight_entries, headcost_for_weight, save_entry


def make_reference_workbook() -> bytes:
    """模拟样例参考表：多段表头、每段列位置不同、克重列名不统一。"""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(["图片", "货号", "单价", "编码", "数量", "总价", None, "单包克重：g", "单包尺寸"])
    sheet.append(["=DISPIMG(\"ID_X1\",1)", "6532", 14, "MB131-260", 100, 1400, None, "99g", "25*35"])
    sheet.append(["=DISPIMG(\"ID_X2\",1)", "1001", 7.9, "MB131-225", 150, 1185, None, "157g", "25*35"])
    sheet.append([None, "9000", 10, "MB131-KG1", 100, 1000, None, "1.2kg", "25*35"])
    sheet.append([None, None, None, None, None, None, None, None, None])
    sheet.append([None, None, "编号", "价格", "数量", None, "编号", "克重/g", "单包尺寸"])
    sheet.append(["=DISPIMG(\"ID_X3\",1)", "黑白/5080", 12, 200, 2400, "MB131-945", None, "174g", "25*35"])
    sheet.append([None, None, None, None, None, None, None, None, None])
    sheet.append(["款式", "货号", "标号", "单价", "数量", None, None, "克重/g", "单包尺寸"])
    sheet.append([None, "MB131-560", None, 5, 100, None, None, "60.5", "20*30"])
    sheet.append([None, None, None, None, None, None, None, None, None])
    sheet.append([None, "货号", "颜色", None, "数量", None, "标签", "单件克重", "单包尺寸"])
    sheet.append([None, "015", "粉色", None, 200, None, "MB131-401", "30克", "14.5*20"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def test_extract_weight_entries_from_multi_block_workbook():
    entries = extract_weight_entries(make_reference_workbook())

    assert entries["MB131-260"] == 99
    assert entries["MB131-225"] == 157
    assert entries["MB131-KG1"] == 1200
    assert entries["MB131-945"] == 174
    assert entries["MB131-560"] == 60.5
    assert entries["MB131-401"] == 30
    # DISPIMG 图片公式里的 ID_ 不能当成编号
    assert not [key for key in entries if key.startswith("ID_")]


def test_headcost_for_weight_rounds_to_two_decimals():
    assert headcost_for_weight(99) == 1.68
    assert headcost_for_weight(174) == 2.96
    assert headcost_for_weight(0) == 0


def test_order_cost_uses_weight_headcost_and_half_discount():
    # 100g → 头程 1.70；2 件 + 操作费 + 续件费：2×(10+1.70) + 7 + 2 = 32.40
    assert calc_order_cost([(10, "单品", 2, "MB131-W")], None, None, None, {"MB131-W": 100}) == 32.40
    # 同时命中减半名单：头程 1.70 / 2 = 0.85：2×(10+0.85) + 7 + 2 = 30.70
    assert calc_order_cost([(10, "单品", 2, "MB131-W")], {"MB131-W": "单品"}, None, None, {"MB131-W": 100}) == 30.70
    # 未命中克重名单：按成本参数头程（单品 5）计算
    assert calc_order_cost([(10, "单品", 2, "MB131-N")], None, None, None, {"MB131-W": 100}) == 39.0


def test_weight_headcost_upsert_and_delete_round_trip():
    category_id = default_category_id()

    saved = save_entry("mb131-w-up", 88.5, category_id, "A")
    assert saved["sku"] == "MB131-W-UP"
    assert saved["weight_g"] == 88.5
    assert saved["headcost"] == round(88.5 * 0.017, 2)
    assert load_weight_entries(category_id, "A")["MB131-W-UP"] == 88.5

    updated = save_entry("MB131-W-UP", 90, category_id, "A")
    assert updated["weight_g"] == 90
    assert delete_entry("MB131-W-UP", category_id, "A") is True
    assert "MB131-W-UP" not in load_weight_entries(category_id, "A")


def test_admin_can_manage_weight_headcost_but_user_cannot():
    admin_password = "weightcost-admin-pass"
    user_password = "weightcost-user-pass"
    create_user("weightcost_admin", hash_password(admin_password), role="admin", status="approved")
    create_user("weightcost_user", hash_password(user_password), status="approved")

    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "weightcost_user", "password": user_password}).status_code == 200
        denied = client.post("/api/admin/weight-headcost", json={"sku": "MB131-W-DENIED", "weight_g": 10})
        assert denied.status_code == 403
        client.post("/api/auth/logout")

        assert client.post("/api/auth/login", json={"username": "weightcost_admin", "password": admin_password}).status_code == 200

        created = client.post("/api/admin/weight-headcost", json={"sku": "mb131-w-api", "weight_g": 100})
        assert created.status_code == 201
        item = created.json()["item"]
        assert item["sku"] == "MB131-W-API"
        assert item["weight_g"] == 100
        assert item["headcost"] == 1.7

        # 重复新增 → 覆盖克重
        overwritten = client.post("/api/admin/weight-headcost", json={"sku": "MB131-W-API", "weight_g": 50.5})
        assert overwritten.status_code == 201
        assert overwritten.json()["item"]["weight_g"] == 50.5

        updated = client.put("/api/admin/weight-headcost/MB131-W-API", json={"weight_g": 60})
        assert updated.status_code == 200
        assert updated.json()["item"]["headcost"] == round(60 * 0.017, 2)

        listed = client.get("/api/weight-headcost", params={"query": "MB131-W-API"})
        assert listed.status_code == 200
        assert listed.json()["items"] == [{"sku": "MB131-W-API", "weight_g": 60.0, "headcost": round(60 * 0.017, 2)}]

        missing = client.put("/api/admin/weight-headcost/MB131-W-ABSENT", json={"weight_g": 10})
        assert missing.status_code == 404

        blank = client.post("/api/admin/weight-headcost", json={"sku": "   ", "weight_g": 10})
        assert blank.status_code in (400, 422)

        bad_weight = client.post("/api/admin/weight-headcost", json={"sku": "MB131-W-BAD", "weight_g": 0})
        assert bad_weight.status_code == 422

        deleted = client.delete("/api/weight-headcost/MB131-W-API")
        assert deleted.status_code == 200
        after_delete = client.put("/api/admin/weight-headcost/MB131-W-API", json={"weight_g": 10})
        assert after_delete.status_code == 404


def test_weight_headcost_import_merges_and_isolates_inventory_category():
    admin_password = "weightcost-import-pass"
    create_user("weightcost_import_admin", hash_password(admin_password), role="admin", status="approved")
    content = make_reference_workbook()

    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "weightcost_import_admin", "password": admin_password}).status_code == 200

        response = client.post(
            "/api/weight-headcost/import",
            data={"inventory_category": "B"},
            files={"file": ("weights.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["incoming"] == 6
        assert payload["added"] == 6
        assert payload["total"] == 6

        in_b = client.get("/api/weight-headcost", params={"query": "MB131-260", "inventory_category": "B"})
        assert in_b.status_code == 200
        assert in_b.json()["items"] == [{"sku": "MB131-260", "weight_g": 99.0, "headcost": 1.68}]

        in_a = client.get("/api/weight-headcost", params={"query": "MB131-260", "inventory_category": "A"})
        assert in_a.status_code == 200
        assert in_a.json()["items"] == []
