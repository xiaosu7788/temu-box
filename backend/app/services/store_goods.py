"""商品查询（只读）：站点表、生命周期列表、商品详情。

数据来源（2026-11 上游更新后改为「生命周期为主 + 销量补全」）：

1. **上新生命周期管理** `POST /api/kiana/mms/robin/searchForSemiSupplier`
   - 行粒度是**商品**，`dataList[].skcList[]` 才是 SKC，这里展平成 SKC 行；
   - 独有 `skcList[].selectStatus`（9 待选品 / 10 待处理 / 12 已加入站点 / 13 待价格校验）
     和 `skcList[].statusTime`（10 节点时间轴）；
   - **没有有效筛选参数**（`selectStatus` 传值无效，total 恒为全部），所以只能拉全量本地过滤；
   - **不返回销量**。

2. **商品列表** `POST /visage-agent-seller/product/skc/pageQuery`（GLO 域）
   - 只用来补 `productTotalSalesVolume` / `last7DaysSalesVolume`（按 `productSkcId` 关联）；
   - 唯一有效的筛选参数是 `skcSiteStatus`，但生命周期里用 `selectStatus==12` 等价推导，不再依赖它。

详情仍走 `product/queryForImage`（没有 mainImageUrl，**主图 = carouselImageUrls[0]**）。
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.database import get_temu_session
from app.services import temu_bridge
from app.services.store_session import StoreError, translate_bridge_error
from app.services.temu_bridge import BridgeError

logger = logging.getLogger("temubox.store.goods")

# 商品列表数据源：
#   goods    —— 商品列表接口 skc/pageQuery（快、稳，默认）
#   lifecycle—— 上新生命周期管理 robin/searchForSemiSupplier（有阶段和时间轴，
#               但在 GLO 域对半托管账号会挂起，甚至把整个账号的请求拖死）
GOODS_SOURCE = os.environ.get("STORE_GOODS_SOURCE", "lifecycle").strip().lower()
LIFECYCLE_SOURCE = "lifecycle"

MAX_PAGE_SIZE = 100
SITE_CACHE_TTL = timedelta(minutes=30)
# 生命周期全量约 3 页（每页 100），60 秒内重复查询直接用缓存
LIFECYCLE_CACHE_TTL = 60.0
# 销量补全最多翻 4 页 × 500 条
SALES_PAGE_SIZE = 500
SALES_MAX_PAGES = 4

# 生命周期状态（名称与 Temu 后台 tab 对齐）
SELECT_STATUS: dict[int, dict] = {
    9: {"name": "已下架", "desc": "已下架（选品阶段已终止）"},
    10: {"name": "未发布到站点", "desc": "已完成价格校验，尚未发布到站点"},
    12: {"name": "已加入站点", "desc": "已发布到站点，可下架"},
    13: {"name": "价格申报中", "desc": "价格申报/校验进行中"},
}

# SKC 时间轴（skcList[].statusTime）
STATUS_TIME_FIELDS: list[tuple[str, str]] = [
    ("createdTime", "创建"),
    ("selectedTime", "选中"),
    ("priceVerificationTime", "价格校验"),
    ("samplePostTime", "寄样"),
    ("samplePostingFinishedTime", "寄样完成"),
    ("qcCompletedTime", "质检完成"),
    ("addedToSiteTime", "加入站点"),
    ("unPublishedTime", "下架"),
    ("firstPurchaseTime", "首单"),
    ("terminatedTime", "终止"),
]

_site_cache: dict[str, tuple[datetime, list[dict]]] = {}
_lifecycle_cache: dict[str, tuple[float, list[dict], list[dict], str]] = {}
_legacy_cache: dict[str, tuple[float, list[dict]]] = {}
_sales_cache: dict[str, tuple[float, dict[str, tuple[Optional[float], Optional[float]]]]] = {}
_cache_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def require_account(user_id: int) -> str:
    """商品接口必须已登录店铺（会话失效时给 401，不自动重登）。"""
    session = get_temu_session(user_id)
    if not session or session["status"] != "active" or not session["account"]:
        raise StoreError("请先登录店铺后再使用商品查询", code="NOT_LOGIN", status=401)
    return session["account"]


def _text(value) -> str:
    return "" if value is None else str(value)


def _number(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso_from_ms(value) -> Optional[str]:
    ms = _number(value)
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


# ==================== 站点表 ====================
def sites(user_id: int, *, force: bool = False) -> list[dict]:
    """104 个站点（bridge 侧有 24h 文件缓存，这里再加 30 分钟内存缓存）。"""
    account = require_account(user_id)
    now = _now()
    with _cache_lock:
        cached = _site_cache.get(account)
        if not force and cached and now - cached[0] < SITE_CACHE_TTL:
            return cached[1]

    try:
        data = temu_bridge.goods_sites(account, force=force)
    except BridgeError as exc:
        raise translate_bridge_error(exc) from None

    items = [
        {
            "site_id": _text(item.get("siteId")),
            "site_name": item.get("siteName") or "",
            "dr": item.get("dr") or "",
        }
        for item in data.get("sites") or []
        if item.get("siteId") is not None
    ]
    with _cache_lock:
        _site_cache[account] = (now, items)
    return items


def invalidate_site_cache(account: Optional[str] = None) -> None:
    with _cache_lock:
        if account:
            _site_cache.pop(account, None)
        else:
            _site_cache.clear()


def invalidate_lifecycle_cache(account: Optional[str] = None) -> None:
    with _cache_lock:
        if account:
            _lifecycle_cache.pop(account, None)
            _legacy_cache.pop(account, None)
            _sales_cache.pop(account, None)
        else:
            _lifecycle_cache.clear()
            _legacy_cache.clear()
            _sales_cache.clear()


def invalidate_cache(account: Optional[str] = None) -> None:
    """登录/退出时统一清理（生命周期 + 销量 + 站点）。"""
    invalidate_lifecycle_cache(account)
    invalidate_site_cache(account)


# ==================== 生命周期 ====================
def _stages(status_time: dict) -> list[dict]:
    stages = []
    for key, label in STATUS_TIME_FIELDS:
        at = (status_time or {}).get(key)
        if not at:
            continue
        stages.append({"key": key, "label": label, "at": _number(at), "date": _iso_from_ms(at)})
    return stages


def _site_of(product: dict) -> tuple[list[str], list[str]]:
    ids: list[str] = []
    names: list[str] = []
    for site in product.get("siteInfoList") or []:
        if site.get("siteId") is not None:
            ids.append(_text(site.get("siteId")))
        if site.get("siteName"):
            names.append(str(site.get("siteName")))
    for site_id in product.get("semiHostedBindSiteIds") or []:
        if site_id is not None and _text(site_id) not in ids:
            ids.append(_text(site_id))
    if product.get("siteName") and product.get("siteName") not in names:
        names.append(str(product.get("siteName")))
    return ids, names


def _flatten(product: dict, sales: dict) -> list[dict]:
    """一个商品可能挂多个 SKC —— 展平成 SKC 行（与旧表格的行粒度一致）。"""
    carousel = product.get("carouselImageUrlList") or []
    main_image = carousel[0] if carousel else ""
    site_ids, site_names = _site_of(product)
    skc_list = product.get("skcList") or []
    rows: list[dict] = []

    for skc in skc_list:
        skc_id = _text(skc.get("skcId"))
        select_status = _number(skc.get("selectStatus"))
        select_status = int(select_status) if select_status is not None else None
        meta = SELECT_STATUS.get(select_status, {})
        status_time = skc.get("statusTime") or {}
        total, seven = sales.get(skc_id, (None, None))
        preview = skc.get("previewImgUrlList") or []
        sku_list = skc.get("skuList") or []
        added_sites = skc.get("addedSiteList") or skc.get("onceAddSiteList") or []

        rows.append({
            "product_id": _text(product.get("productId")),
            "spu_id": _text(product.get("productId")),
            "goods_id": _text(product.get("goodsId")),
            "product_skc_id": skc_id,
            "skc_id": skc_id,
            "product_name": product.get("productName") or "",
            "main_image": main_image or (preview[0] if preview else ""),
            # 参考图里 SKC 下方的 3 张预览图
            "preview_images": preview[:3],
            # 货号：SKC 货号（skcList[].extCode），拿不到才退到商品 goodsId
            "ext_code": _text(skc.get("extCode")) or _text(product.get("goodsId")),
            # SKU：skcList[].skuList[]
            "sku_ids": [_text(item.get("skuId")) for item in sku_list if item.get("skuId")],
            "sku_ext_codes": [_text(item.get("extCode")) for item in sku_list if item.get("extCode")],
            "sku_count": len(sku_list),
            "sku_items": [
                {
                    "sku_id": _text(item.get("skuId")),
                    "ext_code": _text(item.get("extCode")),
                    "price": _text(item.get("supplierPrice")),
                    "goods_sku_id": _text(item.get("goodsSkuId")),
                }
                for item in sku_list[:8]
            ],
            "added_site_count": len(added_sites),
            "category_name": product.get("leafCategoryName") or "",
            "full_category": product.get("fullCategoryName") or [],
            # 生命周期
            "select_status": select_status,
            "status_name": meta.get("name", "未知"),
            "status_desc": meta.get("desc", ""),
            "can_on_sale": skc.get("canOnSale"),
            "can_off_sale": skc.get("canOffSale"),
            "color_name": skc.get("colorName") or "",
            "stages": _stages(status_time),
            "added_to_site_at": _iso_from_ms(status_time.get("addedToSiteTime")),
            "un_published_at": _iso_from_ms(status_time.get("unPublishedTime")),
            # 站点 / 价格
            "site_ids": site_ids,
            "site_names": site_names,
            "supplier_price": _text(skc.get("supplierPrice") or product.get("supplierPrice")),
            "supplier_price_currency": _text(
                skc.get("supplierPriceCurrencyType") or product.get("supplierPriceCurrencyType")
            ),
            # 兼容旧字段（前端仍在用）
            "skc_status": None,
            "site_status": 1 if select_status == 12 else 0,
            "remove_status": product.get("removeStatus"),
            "sales_total": total,
            "sales_7d": seven,
            "created_at": _iso_from_ms(product.get("productCreatedAt")),
            "updated_at": _iso_from_ms(product.get("productUpdatedAt")),
        })
    return rows


def _legacy_row(item: dict) -> dict:
    """商品列表接口的单条记录 → 前端行（没有生命周期阶段/时间轴）。"""
    skc_id = _text(item.get("productSkcId"))
    site_status = 1 if _text(item.get("skcSiteStatus")) == "1" else 0
    skus = item.get("productSkuSummaries") or []
    return {
        "product_id": _text(item.get("productId")),
        "spu_id": _text(item.get("productId")),
        "goods_id": "",
        "product_skc_id": skc_id,
        "skc_id": skc_id,
        "product_name": item.get("productName") or "",
        "main_image": item.get("mainImageUrl") or "",
        "ext_code": item.get("extCode") or "",
        "preview_images": [],
        "sku_ids": [_text(sku.get("productSkuId")) for sku in skus if sku.get("productSkuId")],
        "sku_ext_codes": [],
        "sku_count": len(skus),
        "sku_items": [
            {"sku_id": _text(sku.get("productSkuId")), "ext_code": "", "price": "", "goods_sku_id": ""}
            for sku in skus[:8]
        ],
        "added_site_count": 0,
        "full_category": [],
        "select_status": 12 if site_status else None,
        "status_name": "已加入站点" if site_status else "未发布",
        "status_desc": "商品列表接口：状态按 skcSiteStatus 推导",
        "can_on_sale": None,
        "can_off_sale": None,
        "color_name": "",
        "stages": [],
        "added_to_site_at": None,
        "un_published_at": None,
        "site_ids": [],
        "site_names": [],
        "supplier_price": "",
        "supplier_price_currency": "",
        "skc_status": item.get("skcStatus"),
        "site_status": site_status,
        "remove_status": item.get("removeStatus"),
        "sales_total": _number(item.get("productTotalSalesVolume")),
        "sales_7d": _number(item.get("last7DaysSalesVolume")),
        "created_at": _iso_from_ms(item.get("productCreateTime")),
        "updated_at": None,
    }


def _split_values(raw: Optional[str]) -> list[str]:
    """搜索框支持批量：空格 / 换行 / Tab / 逗号 / 中文逗号 / 顿号分隔。"""
    if not raw:
        return []
    return [part for part in re.split(r"[\s,，、|]+", str(raw)) if part]


def _lifecycle_page(
    account: str,
    *,
    page: int,
    page_size: int,
    keyword: Optional[str],
    search_type: Optional[str],
    site_id: Optional[str],
    select_status: Optional[int],
    site_status: Optional[int],
    time_type: Optional[int],
    time_begin: Optional[int],
    time_end: Optional[int],
    price_review_status: Optional[list] = None,
    sample_qc_status: Optional[list] = None,
    todo_types: Optional[list] = None,
    punish_status: Optional[list] = None,
    limit_status: Optional[list] = None,
    remove_status: Optional[int] = None,
) -> dict:
    """服务端筛选 + 单页：一次只打一个上游请求，避免限流（8~10 次/分钟会 40001）。"""
    status_list = [select_status] if select_status is not None else None
    if status_list is None and site_status is not None:
        # 兼容旧字段：site_status=1 等价「已加入站点」，0 等价其余阶段
        status_list = [12] if site_status == 1 else [9, 10, 13]

    # 搜索框：按用户选择的类型分发到对应服务端参数（支持批量粘贴）
    # 不再做「智能识别」：纯数字到底当 SPU 还是 SKC 无法可靠判断，交给用户明确选择
    mode = (search_type or "name").strip().lower()
    values = _split_values(keyword)
    name_keyword = None
    product_ids = None
    product_skc_ids = None
    product_sku_ids = None
    ext_codes = None
    if values:
        if mode == "spu":
            product_ids = values
        elif mode == "skc":
            product_skc_ids = values
        elif mode == "sku":
            product_sku_ids = values
        elif mode == "extcode":
            ext_codes = values
        else:  # name（默认）：商品名模糊
            name_keyword = " ".join(values)

    data = temu_bridge.goods_lifecycle(
        account,
        page_num=page,
        page_size=page_size,
        select_status=status_list,
        keyword=name_keyword,
        product_ids=product_ids,
        product_skc_ids=product_skc_ids,
        product_sku_ids=product_sku_ids,
        skc_ext_codes=ext_codes,
        site_ids=[site_id] if site_id else None,
        time_type=time_type,
        time_begin=time_begin,
        time_end=time_end,
        remove_status=remove_status,
        price_review_status=price_review_status,
        sample_qc_status=sample_qc_status,
        todo_types=todo_types,
        punish_status=punish_status,
        limit_status=limit_status,
    )
    rows: list[dict] = []
    for product in data.get("items") or []:
        rows.extend(_flatten(product, {}))

    return {
        "total": int(data.get("total") or len(rows)),
        "page": page,
        "page_size": page_size,
        "items": rows,
        "keyword": keyword or None,
        "scanned": len(rows),
        "truncated": False,
        "source": "lifecycle",
        "aggregation": data.get("aggregation") or [],
        # 生命周期接口一页按「商品」算，展平后 SKC 行数可能更多
        "paged_by": "product",
    }


def _goods_page(
    account: str,
    *,
    page: int,
    page_size: int,
    keyword: Optional[str],
    site_id: Optional[str],
    site_status: Optional[int],
) -> dict:
    data = temu_bridge.goods_list(
        account,
        page=page,
        page_size=page_size,
        bind_site_ids=site_id,
        skc_site_status=site_status,
        product_name=keyword or None,
    )
    rows = [_legacy_row(item) for item in data.get("items") or []]
    return {
        "total": int(data.get("total") or len(rows)),
        "page": page,
        "page_size": page_size,
        "items": rows,
        "keyword": keyword or None,
        "scanned": len(rows),
        "truncated": False,
        "source": "goods",
        "aggregation": [],
        "paged_by": "skc",
    }


def list_goods(
    user_id: int,
    *,
    page: int = 1,
    page_size: int = 20,
    site_id: Optional[str] = None,
    keyword: Optional[str] = None,
    search_type: Optional[str] = None,
    site_status: Optional[int] = None,
    select_status: Optional[int] = None,
    time_type: Optional[int] = None,
    time_begin: Optional[int] = None,
    time_end: Optional[int] = None,
    price_review_status: Optional[list] = None,
    sample_qc_status: Optional[list] = None,
    todo_types: Optional[list] = None,
    punish_status: Optional[list] = None,
    limit_status: Optional[list] = None,
    remove_status: Optional[int] = None,
    force: bool = False,
) -> dict:
    """商品列表（行粒度 = SKC）。

    默认走「上新生命周期管理」接口并**使用服务端筛选**（一次一页），
    不再全量翻页 —— 平台连续 8~10 次请求/分钟会 40001，把整个账号拖死。
    生命周期接口异常时回退商品列表接口（`skc/pageQuery`）。
    """
    account = require_account(user_id)
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 20), MAX_PAGE_SIZE))
    keyword = (keyword or "").strip()
    site_id = (site_id or "").strip() or None

    if GOODS_SOURCE == LIFECYCLE_SOURCE:
        try:
            return _lifecycle_page(
                account,
                page=page,
                page_size=page_size,
                keyword=keyword,
                search_type=search_type,
                site_id=site_id,
                select_status=select_status,
                site_status=site_status,
                time_type=time_type,
                time_begin=time_begin,
                time_end=time_end,
                price_review_status=price_review_status,
                sample_qc_status=sample_qc_status,
                todo_types=todo_types,
                punish_status=punish_status,
                limit_status=limit_status,
                remove_status=remove_status,
            )
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, StoreError):
                raise
            if isinstance(exc, BridgeError) and str(exc.code) in ("NOT_LOGIN", "FORBIDDEN", "NOT_CONFIGURED"):
                raise translate_bridge_error(exc) from None
            logger.warning("lifecycle 查询失败，回退商品列表接口：%s", exc)

    return _goods_page(
        account,
        page=page,
        page_size=page_size,
        keyword=keyword if (search_type or "name").lower() in ("", "auto", "name") else None,
        site_id=site_id,
        site_status=site_status,
    )


def quick_filters(user_id: int) -> dict:
    """生命周期快捷筛选计数 / 待办计数（给前端下拉做数量提示）。"""
    account = require_account(user_id)
    out = {"filters": [], "todo_total": 0, "todo_items": []}
    try:
        out["filters"] = temu_bridge.goods_lifecycle_filters(account).get("items") or []
    except BridgeError as exc:
        logger.info("快捷筛选计数失败：%s", exc.message)
    try:
        todo = temu_bridge.goods_lifecycle_todo(account)
        out["todo_total"] = int(todo.get("total") or 0)
        out["todo_items"] = todo.get("items") or []
    except BridgeError as exc:
        logger.info("待办计数失败：%s", exc.message)
    return out

def _detail(data: dict) -> dict:
    images = data.get("images") or {}
    videos = data.get("videos") or {}
    return {
        "product_id": _text(data.get("productId")),
        "product_name": data.get("productName") or "",
        "main_image": data.get("mainImage") or "",
        "carousel": images.get("carousel") or data.get("carouselImageUrls") or [],
        "material_image": images.get("material"),
        "outer_package": images.get("outerPackage") or [],
        "decoration": images.get("decoration") or [],
        "videos": {
            "carousel": videos.get("carousel") or [],
            "detail": videos.get("detail") or [],
        },
        "skc_list": [
            {
                "product_skc_id": _text(skc.get("productSkcId")),
                "color_image": skc.get("colorImageUrl"),
                "preview_images": skc.get("previewImgUrls") or [],
                "skus": [
                    {
                        "product_sku_id": _text(sku.get("productSkuId")),
                        "thumb": sku.get("thumbUrl"),
                    }
                    for sku in skc.get("skus") or []
                ],
            }
            for skc in data.get("skcList") or []
        ],
        "categories": data.get("categories"),
        "i18n": [
            {"language": item.get("language"), "name": item.get("productName")}
            for item in data.get("i18n") or []
        ],
    }


def goods_detail(user_id: int, product_id: str) -> dict:
    account = require_account(user_id)
    product_id = _text(product_id).strip()
    if not product_id:
        raise StoreError("缺少商品 ID", code="ARGS", status=400)

    try:
        data = temu_bridge.goods_detail(account, product_id)
    except BridgeError as exc:
        raise translate_bridge_error(exc) from None
    return _detail(data)


def product_lifecycle(user_id: int, product_id: str) -> dict:
    """单个商品的 SKC 生命周期 / 时间轴（详情抽屉用）：按 productId 服务端筛选，一次请求。"""
    account = require_account(user_id)
    product_id = _text(product_id).strip()
    if not product_id:
        raise StoreError("缺少商品 ID", code="ARGS", status=400)

    rows: list[dict] = []
    properties: list[dict] = []
    try:
        data = temu_bridge.goods_lifecycle(account, page_num=1, page_size=5, product_ids=[product_id])
        for product in data.get("items") or []:
            if _text(product.get("productId")) == product_id:
                rows.extend(_flatten(product, {}))
                properties = [
                    {
                        "name": _text(item.get("name")),
                        "value": _text(item.get("value")),
                        "important": bool(item.get("important")),
                    }
                    for item in product.get("productPropertyList") or []
                ]
    except Exception as exc:  # noqa: BLE001 - 时间轴是增强信息，失败不影响详情
        logger.info("查询生命周期失败（%s）：%s", product_id, exc)
        return {"product_id": product_id, "skc_list": [], "properties": [], "available": False}

    if not rows:
        return {"product_id": product_id, "skc_list": [], "properties": properties, "available": True}
    return {
        "product_id": product_id,
        "product_name": rows[0]["product_name"],
        "available": True,
        "properties": properties,
        "skc_list": [
            {
                "skc_id": row["skc_id"],
                "color_name": row["color_name"],
                "select_status": row["select_status"],
                "status_name": row["status_name"],
                "status_desc": row["status_desc"],
                "can_on_sale": row["can_on_sale"],
                "can_off_sale": row["can_off_sale"],
                "stages": row["stages"],
            }
            for row in rows
        ],
    }
