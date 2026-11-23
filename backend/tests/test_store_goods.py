"""商品查询（生命周期列表 / 站点 / 详情）的行为测试，bridge 全部打桩。

数据源已改为「上新生命周期管理」接口：
- 主数据 `goods_lifecycle_all`（商品 → skcList 展平成 SKC 行）
- 销量由 `goods_list` 补（按 productSkcId 关联）
"""
import pytest
from fastapi.testclient import TestClient

from app.database import create_user, upsert_temu_session
from app.main import app
from app.services import credential_cipher, store_goods, store_session, temu_bridge
from app.services.auth import hash_password
from app.services.temu_bridge import BridgeError

ACCOUNT = "goods-demo@example.com"


def _make_client(username: str, password: str) -> TestClient:
    user = create_user(username, hash_password(password), status="approved", store_access=True)
    upsert_temu_session(
        user["id"],
        account=ACCOUNT,
        account_hash=store_session.account_hash(ACCOUNT),
        account_masked=store_session.mask_account(ACCOUNT),
        mall_id="100001",
        mall_name="演示店铺",
        status="active",
    )
    client = TestClient(app)
    client.__enter__()
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def _product(index: int, name: str, skcs: list[dict], *, site_id: int = 100) -> dict:
    return {
        "productId": 9000 + index,
        "goodsId": 7000 + index,
        "productName": name,
        "productCreatedAt": 1789117108000,
        "productUpdatedAt": 1789117200000,
        "removeStatus": 0,
        "siteName": "美国站",
        "siteInfoList": [{"siteId": site_id, "siteName": "美国站", "stepId": 1}],
        "semiHostedBindSiteIds": [site_id],
        "carouselImageUrlList": [f"https://img.example.com/{index}.jpg", f"https://img.example.com/{index}b.jpg"],
        "leafCategoryName": "大码情趣连衣裙",
        "fullCategoryName": ["服装", "女士时尚", "大码女装"],
        "supplierPrice": "154.00¥",
        "supplierPriceCurrencyType": "CNY",
        "skcList": skcs,
    }


def _skc(skc_id: int, status: int, *, off_sale=None, color="混合色") -> dict:
    return {
        "skcId": skc_id,
        "goodsSkcId": skc_id + 100000,
        "selectStatus": status,
        "colorName": color,
        "canOnSale": None,
        "canOffSale": off_sale,
        "supplierPrice": "154.00¥",
        "supplierPriceCurrencyType": "CNY",
        "previewImgUrlList": [f"https://img.example.com/skc{skc_id}.jpg"],
        "statusTime": {
            "createdTime": 1792750556522,
            "selectedTime": 1792751036572,
            "priceVerificationTime": 1792803827681,
            "addedToSiteTime": 1794808747049 if status == 12 else None,
            "unPublishedTime": None,
        },
    }


@pytest.fixture()
def bridge(monkeypatch):
    store_goods.invalidate_cache()
    state = {
        "products": [
            _product(1, "蕾丝吊带睡裙两件套", [
                _skc(8001, 12, off_sale=True, color="黑色"),
                _skc(8002, 13),
            ]),
            _product(2, "渔网连体衣", [_skc(8003, 9)], site_id=101),
        ],
        "lifecycle_calls": [],
        "sites_calls": 0,
        "detail": {
            "productId": 9001,
            "productName": "蕾丝吊带睡裙两件套",
            "mainImage": "https://img.example.com/1.jpg",
            "carouselImageUrls": ["https://img.example.com/1.jpg", "https://img.example.com/1b.jpg"],
            "images": {
                "carousel": ["https://img.example.com/1.jpg", "https://img.example.com/1b.jpg"],
                "material": None,
                "outerPackage": [],
                "decoration": [],
            },
            "videos": {"carousel": [], "detail": []},
            "skcList": [
                {
                    "productSkcId": 8001,
                    "colorImageUrl": "https://img.example.com/c1.jpg",
                    "previewImgUrls": ["https://img.example.com/p1.jpg"],
                    "skus": [{"productSkuId": 6001, "thumbUrl": "https://img.example.com/t1.jpg"}],
                }
            ],
            "categories": {"cat1": {"catName": "女装"}},
            "i18n": [{"language": "en", "productName": "Lace Slip Dress"}],
        },
        "fail": None,
    }

    def fake_lifecycle(account, **kwargs):
        """模拟服务端筛选：一次只返回一页。"""
        state["lifecycle_calls"].append(kwargs)
        if state["fail"]:
            raise state["fail"]
        products = state["products"]
        status_list = kwargs.get("select_status")
        if status_list:
            products = [
                p for p in products
                if any(s.get("selectStatus") in status_list for s in p["skcList"])
            ]
        keyword = kwargs.get("keyword")
        if keyword:
            products = [p for p in products if keyword in p["productName"]]
        product_ids = kwargs.get("product_ids")
        if product_ids:
            products = [p for p in products if str(p["productId"]) in {str(x) for x in product_ids}]
        skc_total = sum(len(p["skcList"]) for p in products)
        aggregation = []
        if not status_list and not keyword and not product_ids:
            aggregation = [
                {"select_status": 9, "status_name": "待选品", "count": 1},
                {"select_status": 12, "status_name": "已加入站点", "count": 1},
                {"select_status": 13, "status_name": "待价格校验", "count": 1},
            ]
        return {
            "total": skc_total,
            "items": products,
            "aggregation": aggregation,
            "scanned": len(products),
        }

    def fake_goods_list(account, *, page=1, page_size=20, bind_site_ids=None, site=None,
                        skc_site_status=None, product_name=None, product_skc_ids=None):
        # 故意不跟随 state["fail"]：回退用例要验证「lifecycle 挂了，goods 仍可用」
        items = [
            {
                "productId": 9001,
                "productSkcId": 8001,
                "productName": "兜底商品",
                "skcSiteStatus": 1,
                "removeStatus": 0,
                "productTotalSalesVolume": 12,
                "last7DaysSalesVolume": 3,
            }
        ]
        return {"code": "1000000", "total": len(items), "items": items}

    def fake_sites(account, *, force=False):
        state["sites_calls"] += 1
        return {"total": 2, "sites": [{"siteId": 100, "siteName": "美国", "dr": "us"}, {"siteId": 101, "siteName": "加拿大", "dr": "us"}]}

    def fake_detail(account, product_id):
        if state["fail"]:
            raise state["fail"]
        return dict(state["detail"])

    monkeypatch.setattr(temu_bridge, "goods_lifecycle", fake_lifecycle)
    monkeypatch.setattr(temu_bridge, "goods_list", fake_goods_list)
    monkeypatch.setattr(temu_bridge, "goods_sites", fake_sites)
    monkeypatch.setattr(temu_bridge, "goods_detail", fake_detail)
    return state


def test_goods_requires_active_session():
    create_user("goods_no_session", hash_password("pass-no-session"), status="approved", store_access=True)
    client = TestClient(app)
    with client:
        assert client.post("/api/auth/login", json={"username": "goods_no_session", "password": "pass-no-session"}).status_code == 200
        response = client.get("/api/store/goods")
        assert response.status_code == 401
        assert "登录店铺" in response.json()["detail"]
        assert client.get("/api/store/sites").status_code == 401


def test_lifecycle_rows_are_flattened_per_skc(bridge):
    client = _make_client("goods_list_user", "pass-goods-1")
    try:
        response = client.get("/api/store/goods", params={"page_size": 10})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["source"] == "lifecycle"
        assert data["paged_by"] == "product"
        # 2 个商品 → 3 个 SKC 行
        assert len(data["items"]) == 3
        agg = {item["select_status"]: item["count"] for item in data["aggregation"]}
        assert agg == {9: 1, 12: 1, 13: 1}

        row = data["items"][0]
        assert row["product_id"] == "9001"
        assert row["skc_id"] == "8001"
        assert row["goods_id"] == "7001"
        assert row["status_name"] == "已加入站点"
        assert row["can_off_sale"] is True
        assert row["site_status"] == 1
        keys = [stage["key"] for stage in row["stages"]]
        assert "createdTime" in keys and "addedToSiteTime" in keys
        assert "unPublishedTime" not in keys
        assert row["site_ids"] == ["100"]
    finally:
        client.__exit__(None, None, None)


def test_filters_are_sent_to_server(bridge):
    """筛选必须走服务端参数（一次一页），不能拉全量后在本地过滤。"""
    client = _make_client("goods_filter_user", "pass-goods-2")
    try:
        data = client.get("/api/store/goods", params={"select_status": 12}).json()
        # 服务端按商品筛：命中的商品会带回它自己的全部 SKC
        assert data["total"] == 2
        call = bridge["lifecycle_calls"][-1]
        assert call["select_status"] == [12]
        assert call["page_num"] == 1

        client.get("/api/store/goods", params={"keyword": "渔网"})
        assert bridge["lifecycle_calls"][-1]["keyword"] == "渔网"

        client.get("/api/store/goods", params={"site": "101"})
        assert bridge["lifecycle_calls"][-1]["site_ids"] == ["101"]

        # 旧字段 site_status=1 等价「已加入站点」
        client.get("/api/store/goods", params={"site_status": 1})
        assert bridge["lifecycle_calls"][-1]["select_status"] == [12]
        # 0 等价其余阶段
        client.get("/api/store/goods", params={"site_status": 0})
        assert bridge["lifecycle_calls"][-1]["select_status"] == [9, 10, 13]

        # 时间范围（1 创建 / 4 价格确认 / 6 加入站点 / 7 下架）
        client.get("/api/store/goods", params={"time_type": 6, "time_begin": 1, "time_end": 2})
        call = bridge["lifecycle_calls"][-1]
        assert call["time_type"] == 6 and call["time_begin"] == 1 and call["time_end"] == 2

        # 分页参数用 pageNum（不是 page）
        client.get("/api/store/goods", params={"page": 3, "page_size": 50})
        call = bridge["lifecycle_calls"][-1]
        assert call["page_num"] == 3 and call["page_size"] == 50
    finally:
        client.__exit__(None, None, None)


def test_falls_back_to_goods_api_when_lifecycle_fails(bridge):
    """生命周期接口挂住/超时时，回退商品列表接口，不能把整页打成 500。"""
    client = _make_client("goods_fallback_user", "{{SECRET_fb7x2mqp}}")
    try:
        bridge["fail"] = TimeoutError("timed out")
        response = client.get("/api/store/goods")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["source"] == "goods"
        assert data["items"][0]["product_name"] == "兜底商品"
        assert data["items"][0]["sales_total"] == 12.0
    finally:
        client.__exit__(None, None, None)


def test_product_lifecycle_returns_stages(bridge):
    client = _make_client("goods_lc_user", "pass-goods-4")
    try:
        data = client.get("/api/store/goods/9001/lifecycle").json()
        assert data["product_id"] == "9001"
        assert data["available"] is True
        assert len(data["skc_list"]) == 2
        first = data["skc_list"][0]
        assert first["skc_id"] == "8001"
        assert first["status_name"] == "已加入站点"
        assert first["can_off_sale"] is True
        assert any(stage["label"] == "加入站点" for stage in first["stages"])
        # 走 productId 服务端筛选
        assert bridge["lifecycle_calls"][-1]["product_ids"] == ["9001"]
    finally:
        client.__exit__(None, None, None)


def test_goods_detail_maps_carousel_and_i18n(bridge):
    client = _make_client("goods_detail_user", "pass-goods-5")
    try:
        response = client.get("/api/store/goods/9001")
        assert response.status_code == 200, response.text
        data = response.json()
        # 主图 = carousel 第一张（queryForImage 没有 mainImageUrl）
        assert data["main_image"] == "https://img.example.com/1.jpg"
        assert data["carousel"][1] == "https://img.example.com/1b.jpg"
        assert data["skc_list"][0]["skus"][0]["product_sku_id"] == "6001"
        assert data["i18n"][0] == {"language": "en", "name": "Lace Slip Dress"}
        assert data["categories"]["cat1"]["catName"] == "女装"
    finally:
        client.__exit__(None, None, None)


def test_bridge_not_login_maps_to_401_with_relogin_hint(bridge):
    client = _make_client("goods_expired_user", "pass-goods-6")
    try:
        bridge["fail"] = BridgeError("NOT_LOGIN", "该账号尚未登录", stage="session", status=401)
        response = client.get("/api/store/goods")
        assert response.status_code == 401
        assert "重新登录" in response.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_site_table_is_cached_between_requests(bridge):
    client = _make_client("goods_sites_user", "pass-goods-7")
    try:
        first = client.get("/api/store/sites")
        second = client.get("/api/store/sites")
        assert first.status_code == 200 and second.status_code == 200
        assert first.json()["items"][0]["site_name"] == "美国"
        assert bridge["sites_calls"] == 1, "30 分钟内的第二次请求应命中内存缓存"
        # force=true 强制回源
        third = client.get("/api/store/sites", params={"force": "true"})
        assert third.status_code == 200
        assert bridge["sites_calls"] == 2
    finally:
        client.__exit__(None, None, None)