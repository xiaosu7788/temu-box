import io

from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.main import app
from app.database import load_half_entries, merge_half_entries, upsert_half_entry
from app.services.categories import default_category_id


def test_upsert_half_entry_creates_and_updates():
    category_id = default_category_id()

    created = upsert_half_entry(category_id, "MB131-HALF-UP", "单品", "A")
    assert created["sku"] == "MB131-HALF-UP"
    assert created["set_type"] == "单品"
    assert created["inventory_category"] == "A"
    assert load_half_entries(category_id, "A")["MB131-HALF-UP"] == "单品"

    updated = upsert_half_entry(category_id, "MB131-HALF-UP", "6件套", "A")
    assert updated["set_type"] == "6件套"
    assert load_half_entries(category_id, "A")["MB131-HALF-UP"] == "6件套"

    # 不同库存类目互不影响
    other = upsert_half_entry(category_id, "MB131-HALF-UP", "4件套", "B")
    assert other["inventory_category"] == "B"
    assert load_half_entries(category_id, "B")["MB131-HALF-UP"] == "4件套"
    assert load_half_entries(category_id, "A")["MB131-HALF-UP"] == "6件套"


def test_admin_can_create_and_update_half_headcost_but_user_cannot():
    from app.database import create_user
    from app.services.auth import hash_password

    create_user("halfcost_admin", hash_password("halfcostadmin123"), role="admin", status="approved")
    create_user("halfcost_user", hash_password("halfcostuser123"), status="approved")

    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "halfcost_user", "password": "halfcostuser123"}).status_code == 200
        denied = client.post("/api/admin/half-headcost", json={"sku": "MB131-HALF-DENIED", "set_type": "单品"})
        assert denied.status_code == 403
        client.post("/api/auth/logout")

        assert client.post("/api/auth/login", json={"username": "halfcost_admin", "password": "halfcostadmin123"}).status_code == 200

        created = client.post("/api/admin/half-headcost", json={"sku": "mb131-half-api", "set_type": "单品"})
        assert created.status_code == 201
        assert created.json()["item"]["sku"] == "MB131-HALF-API"
        assert created.json()["item"]["set_type"] == "单品"

        # 重复新增 → 覆盖类型
        overwritten = client.post("/api/admin/half-headcost", json={"sku": "MB131-HALF-API", "set_type": "6件套"})
        assert overwritten.status_code == 201
        assert overwritten.json()["item"]["set_type"] == "6件套"

        updated = client.put("/api/admin/half-headcost/MB131-HALF-API", json={"set_type": "4件套"})
        assert updated.status_code == 200
        assert updated.json()["item"]["set_type"] == "4件套"

        listed = client.get("/api/half-headcost", params={"query": "MB131-HALF-API"})
        assert listed.status_code == 200
        assert listed.json()["items"] == [{"sku": "MB131-HALF-API", "set_type": "4件套"}]

        missing = client.put("/api/admin/half-headcost/MB131-HALF-ABSENT", json={"set_type": "单品"})
        assert missing.status_code == 404
        assert missing.json()["detail"] == "SKU 不在头程减半名单中"

        blank = client.post("/api/admin/half-headcost", json={"sku": "   ", "set_type": "单品"})
        assert blank.status_code in (400, 422)

        deleted = client.delete("/api/half-headcost/MB131-HALF-API")
        assert deleted.status_code == 200
        after_delete = client.put("/api/admin/half-headcost/MB131-HALF-API", json={"set_type": "单品"})
        assert after_delete.status_code == 404


def test_half_headcost_endpoints_isolate_category_and_inventory_category():
    from app.database import create_user
    from app.services.auth import hash_password

    create_user("halfcost_scope_admin", hash_password("halfcostscope123"), role="admin", status="approved")

    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "halfcost_scope_admin", "password": "halfcostscope123"}).status_code == 200

        assert client.post("/api/admin/half-headcost", json={"sku": "MB131-HALF-B", "set_type": "单品", "inventory_category": "B"}).status_code == 201

        in_b = client.get("/api/half-headcost", params={"query": "MB131-HALF-B", "inventory_category": "B"})
        assert in_b.status_code == 200
        assert in_b.json()["items"] == [{"sku": "MB131-HALF-B", "set_type": "单品"}]

        in_a = client.get("/api/half-headcost", params={"query": "MB131-HALF-B", "inventory_category": "A"})
        assert in_a.status_code == 200
        assert in_a.json()["items"] == []

        # A 类目下不存在该条目 → 编辑返回 404
        assert client.put("/api/admin/half-headcost/MB131-HALF-B", json={"set_type": "4件套", "inventory_category": "A"}).status_code == 404


def test_extracted_skus_are_uppercased():
    """B5：提取层统一大写，否则接口层 upper() 查询会 404、勾选「保留旧值」也会失效。"""
    from app.services.half_headcost import extract_sku_types

    # 构造一个 SKU 后缀含小写的表
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["SKU", "类型"])
    sheet.append(["MB131-9a9b", "单品"])
    import io

    buffer = io.BytesIO()
    workbook.save(buffer)
    result = extract_sku_types(buffer.getvalue())
    assert "MB131-9A9B" in result, f"应统一大写，实际键：{list(result)}"
    assert all(key == key.upper() for key in result)


def test_seed_is_not_reimported_after_list_is_cleared():
    """B6：名单被清空后，种子文件不应被重新导入（否则删除操作被静默撤销）。"""
    from app.config import HALF_HEADCOST_SEED_PATH
    from app.services.half_headcost import delete_entry, load_entries

    if not HALF_HEADCOST_SEED_PATH.exists():
        return  # 无种子文件的部署环境跳过

    category_id = default_category_id()
    original = dict(load_entries(category_id, "A"))
    try:
        # 首次加载会写入标记位
        load_entries(category_id, "A")
        for sku in list(original):
            delete_entry(sku, category_id, "A")
        assert load_half_entries(category_id, "A") == {}
        # 再次加载不应复活种子
        assert load_entries(category_id, "A") == {}
    finally:
        if original:
            merge_half_entries(category_id, original, "A")
