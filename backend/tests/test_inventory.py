from openpyxl import Workbook
from fastapi.testclient import TestClient

from app.main import app
from app.services.auth import admin_user
from app.database import create_inventory_item, update_inventory_item, get_inventory_catalog, save_inventory_catalog
from app.services.inventory import build_price_catalog, extract_set_type, resolve_profile


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


def test_out_of_range_values_are_skipped_in_favor_of_real_price(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "库存0"
    sheet.cell(1, 8, "MB131-TEST4")
    sheet.cell(1, 9, 300)
    sheet.cell(1, 10, 14.14)
    path = tmp_path / "range.xlsx"
    workbook.save(path)

    catalog = build_price_catalog(path)

    assert catalog["MB131-TEST4"]["price"] == 14.14
    assert catalog["MB131-TEST4"]["source_column"] == 10

def _make_workbook_with(sku: str, price: float, set_type: str = "单品") -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "库存0"
    sheet.cell(1, 8, sku)
    sheet.cell(1, 9, set_type)
    sheet.cell(1, 10, price)
    return workbook


def test_inventory_upload_preview_and_selective_apply(tmp_path, monkeypatch):
    from app.services import inventory as inventory_service
    from app.database import save_inventory_catalog

    # 暂存路径与目标路径都改到 tmp_path（按类目隔离的暂存文件命名）
    monkeypatch.setattr(inventory_service, "_pending_states", {})
    monkeypatch.setattr(inventory_service, "INVENTORY_PATH", tmp_path / "inventory.xlsx")
    monkeypatch.setattr(inventory_service, "PRICE_CACHE_PATH", tmp_path / "cache.json")
    monkeypatch.setenv("PENDING_UPLOAD_PATH", str(tmp_path / "pending.xlsx"))
    monkeypatch.setenv("PENDING_CATALOG_PATH", str(tmp_path / "pending_catalog.json"))
    # 让 _pending_paths 重新读取环境变量（默认在 import 时绑定，这里直接 patch 函数返回值）
    monkeypatch.setattr(
        inventory_service, "_pending_paths",
        lambda category="A": (
            tmp_path / ("pending.xlsx" if (category or "A").upper() == "A" else f"pending_{category}.xlsx"),
            tmp_path / ("pending_catalog.json" if (category or "A").upper() == "A" else f"pending_{category}_catalog.json"),
        ),
    )

    # 当前数据库里：A 旧价 10；B 旧价 20
    old_catalog = {
        "MB131-A": {"sku": "MB131-A", "price": 10.0, "set_type": "单品", "source_sheet": "旧表", "source_row": 1, "source_column": 10},
        "MB131-B": {"sku": "MB131-B", "price": 20.0, "set_type": "单品", "source_sheet": "旧表", "source_row": 2, "source_column": 10},
    }
    save_inventory_catalog({"path": str(tmp_path / "inventory.xlsx"), "size": 1, "mtime_ns": 1, "parser_version": 3}, old_catalog, "A")

    # 新表：A 涨到 15（会变更）、B 不变、C 是新增
    new_file = tmp_path / "new.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "库存0"
    ws.cell(1, 8, "MB131-A")
    ws.cell(1, 9, "单品")
    ws.cell(1, 10, 15.0)
    ws.cell(2, 8, "MB131-B")
    ws.cell(2, 9, "单品")
    ws.cell(2, 10, 20.0)
    ws.cell(3, 8, "MB131-C")
    ws.cell(3, 9, "单品")
    ws.cell(3, 10, 30.0)
    wb.save(new_file)

    state = inventory_service.stage_pending_upload(new_file, "A")
    assert state["sku_count"] == 3
    assert state["inventory_category"] == "A"
    assert [item["sku"] for item in state["diff"]["changed"]] == ["MB131-A"]
    assert [item["sku"] for item in state["diff"]["added"]] == ["MB131-C"]
    assert [item["sku"] for item in state["diff"]["removed"]] == []

    # 管理员选择保留 A 的旧值、跳过新增 C
    result = inventory_service.apply_pending_upload(["MB131-A"], ["MB131-C"], "A")
    merged = result["merged"]
    assert merged["MB131-A"]["price"] == 10.0  # 保留旧值
    assert merged["MB131-B"]["price"] == 20.0  # 未变
    assert "MB131-C" not in merged  # 跳过新增
    assert (tmp_path / "inventory.xlsx").exists()
    assert not (tmp_path / "pending.xlsx").exists()


def test_inventory_categories_endpoint_and_isolation(tmp_path, monkeypatch):
    from app.services import inventory as inventory_service
    from app.database import save_inventory_catalog

    # 类目清单
    assert [c["key"] for c in inventory_service.inventory_categories()] == ["A", "B"]

    # B 类目（自动探测模式）：SKU 与价格随意摆放也能识别
    new_file = tmp_path / "b.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"  # B 类目无固定 sheet 映射，任意 sheet 均扫描
    ws.cell(1, 1, "AB-1001")   # SKU 在第1列
    ws.cell(1, 2, 12.5)        # 价格在右侧
    ws.cell(2, 3, "CD-2002")   # SKU 在第3列
    ws.cell(2, 4, "四件套")
    ws.cell(2, 5, 18.0)
    wb.save(new_file)

    catalog = inventory_service.build_price_catalog(new_file, category="B")
    assert catalog["AB-1001"]["price"] == 12.5
    assert catalog["CD-2002"]["price"] == 18.0
    assert catalog["CD-2002"]["set_type"] == "4件套"

    # A/B 暂存互不干扰
    monkeypatch.setattr(inventory_service, "_pending_states", {})
    monkeypatch.setattr(
        inventory_service, "_pending_paths",
        lambda category="A": (
            tmp_path / (f"pending.xlsx" if (category or "A").upper() == "A" else f"pending_{category}.xlsx"),
            tmp_path / (f"pending_catalog.json" if (category or "A").upper() == "A" else f"pending_{category}_catalog.json"),
        ),
    )
    monkeypatch.setattr(inventory_service, "INVENTORY_PATH", tmp_path / "inventory.xlsx")

    # A 上传暂存
    wb_a = Workbook()
    ws_a = wb_a.active
    ws_a.title = "库存0"
    ws_a.cell(1, 8, "MB131-X1")
    ws_a.cell(1, 9, "单品")
    ws_a.cell(1, 10, 5.0)
    file_a = tmp_path / "a.xlsx"
    wb_a.save(file_a)
    inventory_service.stage_pending_upload(file_a, "A")

    # B 上传暂存
    inventory_service.stage_pending_upload(new_file, "B")

    assert inventory_service.pending_upload("A") is not None
    assert inventory_service.pending_upload("B") is not None
    # 取消 A 不影响 B
    inventory_service.discard_pending_upload("A")
    assert inventory_service.pending_upload("A") is None
    assert inventory_service.pending_upload("B") is not None
    assert inventory_service.pending_upload("B")["inventory_category"] == "B"

def test_inventory_items_endpoint_filters_and_paginates(monkeypatch):
    from app.services.auth import current_user
    app.dependency_overrides[current_user] = lambda: {"id": 1, "role": "user", "status": "approved"}
    monkeypatch.setattr(
        "app.main.load_price_catalog",
        lambda category="A": {
            "MB131-A": {"sku": "MB131-A", "price": 17.1, "set_type": "单品"},
            "MB131-B": {"sku": "MB131-B", "price": 22.0, "set_type": "2件套"},
        },
    )

    try:
        response = TestClient(app).get(
            "/api/inventory/items",
            params={"query": "mb131-b", "page": 1, "page_size": 10},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "total": 1,
        "items": [{"sku": "MB131-B", "price": 22.0, "set_type": "2件套"}],
    }


def test_inventory_item_can_be_created_and_updated():
    signature = {"path": "manual-inventory.xlsx", "size": 1, "mtime_ns": 1, "parser_version": 3}
    save_inventory_catalog(signature, {})

    created = create_inventory_item("MB131-MANUAL", 17.1, "单品")
    assert created["sku"] == "MB131-MANUAL"
    assert created["source_sheet"] == "手动维护"
    assert get_inventory_catalog()["MB131-MANUAL"]["price"] == 17.1

    updated = update_inventory_item("MB131-MANUAL", "MB131-MANUAL-2", 18.2, "6件套")
    assert updated["sku"] == "MB131-MANUAL-2"
    assert updated["set_type"] == "6件套"
    assert "MB131-MANUAL" not in get_inventory_catalog()
    assert get_inventory_catalog()["MB131-MANUAL-2"]["price"] == 18.2

def test_admin_can_create_and_update_inventory_item_but_user_cannot():
    from app.database import create_user
    from app.services.auth import hash_password

    admin = create_user("inventory_admin", hash_password("inventoryadmin123"), role="admin", status="approved")
    user = create_user("inventory_user", hash_password("inventoryuser123"), status="approved")
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "inventory_user", "password": "inventoryuser123"}).status_code == 200
        assert client.post("/api/admin/inventory/items", json={"sku": "MB131-DENIED", "price": 1, "set_type": "单品"}).status_code == 403
        client.post("/api/auth/logout")
        assert client.post("/api/auth/login", json={"username": "inventory_admin", "password": "inventoryadmin123"}).status_code == 200
        created = client.post("/api/admin/inventory/items", json={"sku": "mb131-api", "price": 19.5, "set_type": "单品"})
        assert created.status_code == 201
        assert created.json()["item"]["sku"] == "MB131-API"
        updated = client.put("/api/admin/inventory/items/MB131-API", json={"sku": "MB131-API", "price": 20, "set_type": "4件套"})
        assert updated.status_code == 200
        assert updated.json()["item"]["set_type"] == "4件套"


def test_inventory_items_support_multi_condition_filters(monkeypatch):
    from app.services.auth import current_user
    app.dependency_overrides[current_user] = lambda: {"id": 1, "role": "user", "status": "approved"}
    monkeypatch.setattr(
        "app.main.load_price_catalog",
        lambda category="A": {
            "MB131-A": {"sku": "MB131-A", "price": 5.0, "set_type": "单品", "source_sheet": "美东", "source_row": 3},
            "MB131-B": {"sku": "MB131-B", "price": 42.0, "set_type": "6件套", "source_sheet": "库存0", "source_row": 120},
            "MB131-C": {"sku": "MB131-C", "price": 88.0, "set_type": "6件套", "source_sheet": "库存0", "source_row": 900},
        },
    )

    def query(params):
        with TestClient(app) as client:
            return client.get("/api/inventory/items", params=params).json()

    try:
        # 价格区间
        assert [item["sku"] for item in query({"price_min": 10, "price_max": 50})["items"]] == ["MB131-B"]
        # 类型 + 来源工作表
        assert [item["sku"] for item in query({"set_type": "6件套", "source_sheet": "库存0"})["items"]] == ["MB131-B", "MB131-C"]
        # 行号区间
        assert [item["sku"] for item in query({"row_min": 100, "row_max": 500})["items"]] == ["MB131-B"]
        # 多条件为 AND 关系
        assert query({"set_type": "6件套", "price_max": 50})["total"] == 1
        # 空条件返回全部
        assert query({})["total"] == 3
        # 价格缺失的记录在价格区间筛选下被排除
        monkeypatch.setattr(
            "app.main.load_price_catalog",
            lambda category="A": {"MB131-N": {"sku": "MB131-N", "price": None, "set_type": "单品"}},
        )
        assert query({"price_min": 0})["total"] == 0
    finally:
        app.dependency_overrides.clear()


def test_single_query_matches_sku_or_set_type(monkeypatch):
    """单一搜索框：query 同时匹配 SKU 与类型，命中其一即返回。"""
    from app.services.auth import current_user
    app.dependency_overrides[current_user] = lambda: {"id": 1, "role": "user", "status": "approved"}
    monkeypatch.setattr(
        "app.main.load_price_catalog",
        lambda category="A": {
            "MB131-A": {"sku": "MB131-A", "price": 5.0, "set_type": "单品"},
            "MB131-B": {"sku": "MB131-B", "price": 42.0, "set_type": "6件套"},
            "MB131-C": {"sku": "MB131-C", "price": 88.0, "set_type": "4件套"},
        },
    )

    def query(params):
        with TestClient(app) as client:
            return client.get("/api/inventory/items", params=params).json()

    try:
        # 按 SKU 片段匹配
        assert [i["sku"] for i in query({"query": "MB131-B"})["items"]] == ["MB131-B"]
        # 按类型匹配：不写 SKU 也能搜到
        assert [i["sku"] for i in query({"query": "6件套"})["items"]] == ["MB131-B"]
        # 类型片段匹配多个
        assert [i["sku"] for i in query({"query": "件套"})["items"]] == ["MB131-B", "MB131-C"]
        # 大小写不敏感
        assert query({"query": "mb131-a"})["total"] == 1
        # 无匹配
        assert query({"query": "不存在的关键词"})["total"] == 0
        # 空 query 返回全部
        assert query({"query": ""})["total"] == 3
    finally:
        app.dependency_overrides.clear()


def test_set_type_parses_multi_digit_pieces_without_truncation():
    """B4：件数不能被截断（"15件套" 曾误识别为 "5件套"）。"""
    assert extract_set_type(["4件套"]) == "4件套"
    assert extract_set_type(["12件套"]) == "12件套"
    assert extract_set_type(["15件套"]) == "15件套"
    assert extract_set_type(["13件套"]) == "13件套"
    assert extract_set_type(["21件套"]) == "21件套"
    assert extract_set_type(["20件套"]) == "20件套"
    assert extract_set_type(["30件套"]) == "30件套"
    assert extract_set_type(["二十件套"]) == "20件套"
    assert extract_set_type(["二十五件套"]) == "25件套"
    # 名称里带件数也能取出
    assert extract_set_type(["商品名称:15件套组合"]) == "15件套"
    # 其他类型不受影响
    assert extract_set_type(["单品"]) == "单品"
    assert extract_set_type(["套装"]) == "多件套"
    # 超出 2-30 的件数视为无效
    assert extract_set_type(["1件套"]) is None
    assert extract_set_type(["99件套"]) is None


def test_unknown_inventory_category_raises_instead_of_falling_back_to_a():
    """B1：未注册/已停用的库存类目必须报错，不能静默回退到 A（否则会覆盖 A 的数据）。"""
    import pytest

    assert resolve_profile("A")["key"] == "A"
    assert resolve_profile("B")["key"] == "B"
    for bad in ("C", "ZZZ", "不存在"):
        with pytest.raises(ValueError):
            resolve_profile(bad)


def test_unknown_category_endpoints_return_400(monkeypatch):
    """B1：通过接口传未知类目应得到 400，而不是落到 A 类目上。"""
    app.dependency_overrides[admin_user] = lambda: {"id": 1, "role": "admin", "status": "approved"}
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/inventory/rebuild",
                data={"inventory_category": "ZZZ"},
            )
        assert response.status_code == 400
        assert "库存类目" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()
