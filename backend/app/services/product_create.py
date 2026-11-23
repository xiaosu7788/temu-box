"""商品创建：类目模板驱动的手动创建 + Excel 批量创建。

上游能力在 `bridge/lib/product.js`（temu-ops v1.1.0），本模块只做：
表单校验 → 转发 bridge → 统一错误翻译。

关键约束（均为上游实测，不要绕过）：
  - 类目路径必须 10 级全传，缺级报 `9th Level Category ID cannot be empty`
  - 价格单位是「分」，前端填元
  - 重量存「毫克」、体积存「毫米」
  - 单品件数必须为 1 且要带 `pieceUnitCode`
  - 每建一个商品至少 3 个上游请求（draft → save → add）
  - 平台限流约 8~10 次/分钟，所以批量必须串行 + 长间隔
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

from app.services import temu_bridge
from app.database import load_batch_state, log_product_create, save_batch_state
from app.services.store_goods import require_account
from app.services.store_session import StoreError, translate_bridge_error
from app.services.temu_bridge import BridgeError

logger = logging.getLogger("temubox.product_create")

# 批量创建单项间隔：一个商品至少 3 个上游请求，20 秒 ≈ 每分钟 9 个请求，贴着限流线
ITEM_INTERVAL_SECONDS = 20
# 批量上限（20 秒/项 → 50 项约 17 分钟）
MAX_BATCH_ITEMS = 50
MAX_IMAGES = 10

# 半托管商品英文标题硬性下限（product/add 会报 2000177；低于此值平台只回误导性的 6000002）
EN_NAME_MIN = 20
# 类目配置里可能要求、但我们界面上还没有的字段（命中时提示用户去 Temu 后台补齐）
CONFIG_REQUIRED_HINTS = {
    "needGuideFile": "该类目需要上传资质/说明文件",
    "matchManufacturingLocationRequired": "该类目需要填写生产地",
    "matchNetContentRequired": "多件商品需要填写净含量",
    "matchNumberOfPiecesNewRequired": "多件商品需要填写件数",
}

SHIPMENT_LIMITS = ["1个工作日", "2个工作日", "3个工作日", "5个工作日", "7个工作日", "9个工作日", "10个工作日"]


def _bridge(call, *args, **kwargs):
    """统一把 BridgeError 翻译成 StoreError。"""
    try:
        return call(*args, **kwargs)
    except BridgeError as exc:
        raise translate_bridge_error(exc) from None


# ==================== 类目推荐 ====================
def category_suggest(user_id: int, name: str) -> dict:
    """按商品名推荐类目路径。

    中文名走 `productNameZh`、英文名走 `productNameEn`（平台按语言各自匹配），
    只传一个时另一方留空。
    """
    account = require_account(user_id)
    raw = (name or "").strip()
    if not raw:
        raise StoreError("请先填写商品名称再推荐类目", code="ARGS", status=400)
    if len(raw) < 2:
        raise StoreError("商品名太短，至少 2 个字符", code="ARGS", status=400)
    # 含中文按中文名送，否则按英文名送
    has_cn = any("\u4e00" <= ch <= "\u9fff" for ch in raw)
    data = _bridge(
        temu_bridge.product_category_suggest,
        account,
        name_zh=raw if has_cn else "",
        name_en="" if has_cn else raw,
    )
    return {"total": data.get("total") or 0, "paths": data.get("paths") or []}


def constants(user_id: int) -> dict:
    """固定枚举（敏感类型 / SKU 分类），前端别写死数值。"""
    account = require_account(user_id)
    data = _bridge(temu_bridge.product_constants, account)
    return {
        "sku_classification": data.get("skuClassification") or [],
        "sensitive_types": data.get("sensitiveTypes") or [],
    }


def regions(user_id: int, *, refresh: bool = False) -> dict:
    """产地 / 制造地点候选。"""
    account = require_account(user_id)
    data = _bridge(temu_bridge.product_regions, account, refresh=refresh)
    return {
        # 每项统一成 {code, name, shortName}
        # 产地国家用它填 origin.countryShortName（shortName），省份填 origin.region2Id（code）
        "origin_countries": data.get("originCountries") or [],
        "cn_provinces": data.get("cnProvinces") or [],
        "manufacturing_countries": data.get("manufacturingCountries") or [],
    }


def size_templates(user_id: int, cat_id: int, site_id: int = 0, group_id: int = 0) -> dict:
    """尺码表模板：用户保存的尺码表，businessId 即 sizeTemplateIds 的值。

    site_id / group_id 决定按哪个尺码组过滤（站点 100 → US 组）。
    不传的话拿到的是不分组模板，提交时会报 6000012。
    """
    account = require_account(user_id)
    if not cat_id:
        raise StoreError("需要类目 ID", code="ARGS", status=400)
    data = _bridge(temu_bridge.product_size_templates, account, cat_id, site_id, group_id)
    return {
        "total": data.get("total") or 0,
        "group_id": data.get("groupId"),
        "list": data.get("list") or [],
    }


def size_spec_config(user_id: int, cat_id: int) -> dict:
    """尺码体系映射（哪个站点用哪组尺码）。"""
    account = require_account(user_id)
    if not cat_id:
        raise StoreError("需要类目 ID", code="ARGS", status=400)
    return _bridge(temu_bridge.product_size_spec_config, account, cat_id)


def category_config(user_id: int, leaf_cat_id: int, site_id: int = 100) -> dict:
    """类目配置（必填项、标题长度、敏感属性）。"""
    account = require_account(user_id)
    if not leaf_cat_id:
        raise StoreError("需要类目 ID", code="ARGS", status=400)
    data = _bridge(temu_bridge.product_category_config, account, leaf_cat_id, site_id)
    cfg = data.get("config") or {}
    return {
        "config": cfg,
        "english_name_required": bool(cfg.get("matchEnglishNameRequired")),
        "title_max_length": cfg.get("productTitleMaxLength") or 0,
        "spec_max_length": cfg.get("productSpecMaxLength") or 0,
        "sensitive_types": cfg.get("matchSensitiveConfigList") or [],
        "extra_requirements": [
            hint for key, hint in CONFIG_REQUIRED_HINTS.items() if cfg.get(key)
        ],
    }


def category_search(user_id: int, keyword: str, *, is_en: bool = False) -> dict:
    """类目关键字搜索（多条模糊）。这是官网分类搜索框的真实接口。"""
    account = require_account(user_id)
    text = (keyword or "").strip()
    if len(text) < 1:
        raise StoreError("请输入类目关键字", code="ARGS", status=400)
    data = _bridge(temu_bridge.product_category_search, account, text, is_en=is_en)
    return {"total": data.get("total") or 0, "paths": data.get("paths") or []}


def category_hints(user_id: int) -> dict:
    """常用推荐类目。"""
    account = require_account(user_id)
    data = _bridge(temu_bridge.product_category_hints, account)
    return {"total": data.get("total") or 0, "paths": data.get("paths") or []}


def warehouses(user_id: int, site_id: Optional[int] = None) -> dict:
    """发货仓（含名称与覆盖站点）。"""
    account = require_account(user_id)
    data = _bridge(temu_bridge.product_warehouses, account, site_id)
    return {"total": data.get("total") or 0, "items": data.get("items") or []}


def category_children(user_id: int, parent_cat_id: int = 0) -> dict:
    """逐级类目树。"""
    account = require_account(user_id)
    data = _bridge(temu_bridge.product_category_children, account, parent_cat_id)
    return {"parent_cat_id": parent_cat_id, "total": data.get("total") or 0, "nodes": data.get("nodes") or []}


def category_path(user_id: int, cat_ids: list) -> dict:
    """反查类目路径（用于把手填的 ID 显示成中文）。"""
    account = require_account(user_id)
    ids = [int(x) for x in cat_ids if str(x).strip().isdigit()]
    if not ids:
        raise StoreError("请提供 catId", code="ARGS", status=400)
    data = _bridge(temu_bridge.product_category_path, account, ids)
    return {"total": data.get("total") or 0, "list": data.get("list") or []}


# ==================== 模板 / 环境 ====================
def category_template(user_id: int, cat_id: int, *, refresh: bool = False) -> dict:
    """类目模板 + 概要（属性数、规格、控件类型分布）。"""
    account = require_account(user_id)
    data = _bridge(temu_bridge.product_template, account, cat_id, refresh=refresh)
    return {
        "cat_id": cat_id,
        "template_id": (data.get("template") or {}).get("templateId"),
        "summary": data.get("summary") or {},
        "template": data.get("template") or {},
    }


def describe(user_id: int, cat_id: int, selected: Optional[dict] = None) -> dict:
    """按当前已选值返回：应显示字段、缺失项、颜色/尺码选项。"""
    account = require_account(user_id)
    return _bridge(temu_bridge.product_describe, account, cat_id, selected or {})


def sites(user_id: int, *, refresh: bool = False) -> dict:
    account = require_account(user_id)
    return {"items": _bridge(temu_bridge.product_sites, account, refresh=refresh).get("items") or []}


def store_env(user_id: int, site_id: Optional[int] = None, *, refresh: bool = False) -> dict:
    account = require_account(user_id)
    return _bridge(temu_bridge.product_env, account, site_id, refresh=refresh)


def images(
    user_id: int,
    *,
    carousel: bool = False,
    page_no: int = 1,
    page_size: int = 20,
    name: Optional[str] = None,
    proportion: Optional[str] = None,
    material_type: Optional[int] = None,
    folder_id: int = 0,
) -> dict:
    """素材库图片（创建商品只能从素材库选，轮播图 3:4、素材图 1:1）。"""
    account = require_account(user_id)
    return _bridge(
        temu_bridge.product_images,
        account,
        carousel=carousel,
        page_no=page_no,
        page_size=page_size,
        name=name,
        proportion=proportion,
        material_type=material_type,
        folder_id=folder_id,
    )


# ==================== 表单校验 ====================
def _int_list(value) -> list[int]:
    if isinstance(value, list):
        return [int(x) for x in value if str(x).strip().isdigit()]
    return [int(x) for x in str(value or "").replace(">", ",").replace("/", ",").split(",") if x.strip().isdigit()]


def normalize_form(raw: dict) -> dict:
    """把前端/Excel 的松散输入整理成 bridge 需要的 form，并做前置校验。

    校验失败抛 StoreError(400)，错误信息直接给到界面。
    """
    if not isinstance(raw, dict):
        raise StoreError("表单格式不正确", code="ARGS", status=400)

    cat_path = _int_list(raw.get("catPath") or raw.get("cat_path") or [])
    if not cat_path:
        raise StoreError("请选择商品类目", code="ARGS", status=400)

    name = str(raw.get("name") or "").strip()
    if len(name) < 3:
        raise StoreError("商品名称至少 3 个字符", code="ARGS", status=400)
    if len(name) > 500:
        raise StoreError("商品名称过长（上限 500 字符）", code="ARGS", status=400)

    images = [str(x) for x in (raw.get("images") or []) if str(x).strip()][:MAX_IMAGES]
    if not images:
        raise StoreError("至少需要 1 张轮播图", code="ARGS", status=400)

    # 素材图可以留空：SDK 会从首图自动裁 1:1（autoMaterialImg）
    material_img = str(raw.get("materialImgUrl") or raw.get("material_img_url") or "").strip()

    try:
        price = float(raw.get("skuPrice") or raw.get("price") or 0)
    except (TypeError, ValueError):
        raise StoreError("价格格式不正确", code="ARGS", status=400) from None
    if price <= 0:
        raise StoreError("价格必须大于 0", code="ARGS", status=400)

    # 官网明确：创建时库存非必填；不填按 0 处理（buildBody 需要 >0，这里兜底 0 → 1）
    try:
        stock = int(raw.get("stock") or 0)
    except (TypeError, ValueError):
        raise StoreError("库存格式不正确", code="ARGS", status=400) from None
    if stock < 0:
        raise StoreError("库存不能为负", code="ARGS", status=400)

    color_vid = raw.get("colorVid") or raw.get("color_vid")
    # 尺码支持多选（每个尺码一个 SKU）
    size_vids = raw.get("sizeVids") or raw.get("size_vids")
    if not isinstance(size_vids, list) or not size_vids:
        single = raw.get("sizeVid") or raw.get("size_vid")
        size_vids = [single] if single else []
    size_vids = [int(x) for x in size_vids if str(x).strip().isdigit()]
    if not color_vid:
        raise StoreError("请选择颜色规格", code="ARGS", status=400)
    if not size_vids:
        raise StoreError("请至少选择一个尺码", code="ARGS", status=400)

    shipment = str(raw.get("shipmentLimit") or "2个工作日").strip()
    if shipment not in SHIPMENT_LIMITS:
        shipment = "2个工作日"

    selected = raw.get("selected")
    if not isinstance(selected, dict):
        selected = {}

    form = {
        "catPath": cat_path,
        "siteId": int(raw.get("siteId") or raw.get("site_id") or 100),
        "name": name,
        "enName": str(raw.get("enName") or raw.get("en_name") or "").strip(),
        "selected": selected,
        "colorVid": int(color_vid),
        "sizeVid": size_vids[0],
        "sizeVids": size_vids,
        "images": images,
        "materialImgUrl": material_img,
        "skuPrice": round(price, 2),
        "stock": stock,
        "extCode": str(raw.get("extCode") or raw.get("ext_code") or "").strip(),
        "shipmentLimit": shipment,
        # 1 单品 / 2 同款多件装 / 3 混合套装
        "skuClassification": int(raw.get("skuClassification") or 1),
        "pieces": int(raw.get("pieces") or 1),
    }
    # 尺码类型（1 均码 / 2 常规 / 3 Tall）：SDK 预检用它比对模板类型，缺了预检只查一半
    gst = raw.get("generalSizeType")
    if gst is None:
        gst = raw.get("general_size_type")
    if gst:
        try:
            form["generalSizeType"] = int(gst)
        except (TypeError, ValueError):
            pass

    # 多件装才需要「是否独立包装」；之前整个丢掉，切到多件装时这个字段永远传不上去
    if raw.get("individuallyPacked") is not None or raw.get("individually_packed") is not None:
        flag = raw.get("individuallyPacked")
        if flag is None:
            flag = raw.get("individually_packed")
        form["individuallyPacked"] = 1 if flag in (1, True, "1", "true", "True") else 0
    # 用户调整过的发货仓（不传则由 bridge 用该站点默认绑定）
    # 素材语言（图片文案适配的市场语言）
    langs = raw.get("materialMultiLanguages") or raw.get("material_languages")
    if isinstance(langs, list) and langs:
        form["materialMultiLanguages"] = [str(x).strip() for x in langs if str(x).strip()]

    # ---- v1.1.3 补齐项：产地 / 制造地点 / 敏感属性 / SKU 分类 / 运费模板 ----
    if isinstance(raw.get("origin"), dict):
        form["origin"] = {
            "countryShortName": str(raw["origin"].get("countryShortName") or "").strip() or None,
            "region2Id": raw["origin"].get("region2Id") or None,
        }
        form["origin"] = {k: v for k, v in form["origin"].items() if v}
    # 详情图文（goodsLayerDecorationReqs）：每张一个楼层，3:4 / 1350×1800
    deco = raw.get("decorationImages") or raw.get("decoration_images")
    if isinstance(deco, list) and deco:
        form["decorationImages"] = [str(x).strip() for x in deco if str(x).strip()]
    if isinstance(raw.get("manufacturing"), list) and raw["manufacturing"]:
        form["manufacturing"] = [str(x).strip() for x in raw["manufacturing"] if str(x).strip()]
    if "isSensitive" in raw:
        form["isSensitive"] = bool(raw.get("isSensitive"))
    if isinstance(raw.get("sensitiveList"), list) and raw["sensitiveList"]:
        form["sensitiveList"] = [int(x) for x in raw["sensitiveList"] if str(x).strip().isdigit()]
    if isinstance(raw.get("sensitiveTypes"), list) and raw["sensitiveTypes"]:
        form["sensitiveTypes"] = [int(x) for x in raw["sensitiveTypes"] if str(x).strip().isdigit()]
    if str(raw.get("freightTemplateId") or "").strip():
        form["freightTemplateId"] = str(raw["freightTemplateId"]).strip()
    # 发货仓 ID 是字符串（WH-02192184443012443），用 isdigit 过滤会把它们全丢掉
    wh_ids = raw.get("warehouseIds") or raw.get("warehouse_ids")
    if isinstance(wh_ids, list) and wh_ids:
        cleaned = [str(x).strip() for x in wh_ids if str(x).strip()]
        if cleaned:
            form["warehouseIds"] = cleaned
    # 多仓库存：{warehouseId: 数量}，不传则每个仓都等分总库存
    if isinstance(raw.get("stockPerWarehouse"), dict) and raw["stockPerWarehouse"]:
        per = {}
        for wid, qty in raw["stockPerWarehouse"].items():
            key = str(wid).strip()
            if key:
                try:
                    per[key] = max(0, int(qty))
                except (TypeError, ValueError):
                    continue
        if per:
            form["stockPerWarehouse"] = per

    if isinstance(raw.get("weight"), dict):
        form["weight"] = raw["weight"]
    if isinstance(raw.get("volume"), dict):
        form["volume"] = raw["volume"]
    tpl_ids = raw.get("sizeTemplateIds") or raw.get("size_template_ids")
    if isinstance(tpl_ids, list) and tpl_ids:
        form["sizeTemplateIds"] = [int(x) for x in tpl_ids if str(x).strip().isdigit()]

    # 尺码表「蓝本」id：product/add 不接受蓝本 id（会报 6000012 不合法的尺码模板id），
    # 必须由 SDK 先用 createSizeSpec 基于蓝本生成新表再用新 id 提交。
    # 前端传 sizeTemplateSourceId（= 下拉里选中的那张表）即可，SDK 自动建表。
    source_id = raw.get("sizeTemplateSourceId") or raw.get("size_template_source_id")
    if source_id and str(source_id).strip().isdigit():
        form["sizeTemplateSourceId"] = int(source_id)
        form.pop("sizeTemplateIds", None)  # 交给 SDK 生成，避免误把蓝本 id 传下去
    if raw.get("sizeSpecName"):
        form["sizeSpecName"] = str(raw["sizeSpecName"]).strip()[:120]

    # 英文名：半托管硬性 ≥20 字，短了平台只回误导性的 6000002
    en_name = form["enName"]
    if not en_name:
        raise StoreError(
            f"请填写英文商品名（至少 {EN_NAME_MIN} 个字符，半托管商品强制要求）",
            code="ARGS",
            status=400,
        )
    if len(en_name) < EN_NAME_MIN:
        raise StoreError(
            f"英文名太短（{len(en_name)} < {EN_NAME_MIN} 字符），平台会报 2000177/6000002",
            code="ARGS",
            status=400,
        )
    return form


def preview(user_id: int, raw_form: dict) -> dict:
    """组装请求体但不创建（界面上的「预览」）。"""
    account = require_account(user_id)
    form = normalize_form(raw_form)
    data = _bridge(temu_bridge.product_preview, account, form)
    body = data.get("body") or {}
    return {
        "form": form,
        "size": data.get("size") or 0,
        "property_count": data.get("propertyCount") or 0,
        # 只回摘要，避免把几百 KB 的请求体整个丢给前端
        "summary": {
            "cat_id": body.get("catId"),
            "cat_ids": body.get("catIds"),
            "product_name": body.get("productName"),
            "price_cents": ((body.get("productSkuReqs") or [{}])[0] or {}).get("supplierPrice"),
            "stock": ((body.get("productSkuReqs") or [{}])[0] or {}).get("targetStockAvailable"),
            "image_count": len(body.get("carouselImageUrls") or []),
            "sku_count": len(body.get("productSkuReqs") or []),
            "warehouse_ids": body.get("productWarehouseRouteReq", {}).get("targetRouteList")
            if isinstance(body.get("productWarehouseRouteReq"), dict)
            else None,
        },
    }


def create(user_id: int, raw_form: dict, *, dry_run: bool = True, batch: bool = False) -> dict:
    """创建商品。dry_run=True 只走到 draft/save。"""
    name_for_log = str((raw_form or {}).get("name") or "")
    account = require_account(user_id)
    form = normalize_form(raw_form)
    result = _bridge(temu_bridge.product_create, account, form, dry_run=dry_run)

    # v1.1.5：提交前按类目配置做的必填校验（没发请求）
    if result.get("stage") == "validate":
        errors = result.get("errors") or []
        log_product_create(user_id, name_for_log, status="failed",
                           error="校验未通过：" + "；".join(str(x) for x in errors), batch=batch)
        raise StoreError(
            "创建前校验未通过：" + "；".join(str(x) for x in errors),
            code="VALIDATE",
            status=400,
        )
    if not result.get("ok", True):
        # 平台明确拒绝：把上游 code/msg 带回去，不要伪装成功
        log_product_create(user_id, name_for_log, status="failed",
                           error=str(result.get("msg") or "平台未通过"), batch=batch)
        raise StoreError(
            f"创建失败：{result.get('msg') or '平台未通过'}（code={result.get('code')}）",
            code="CREATE_REJECTED",
            status=400,
        )
    log_product_create(
        user_id,
        name_for_log,
        status="ok",
        product_id=result.get("productId"),
        batch=batch,
    )
    return {
        "ok": True,
        "dry_run": dry_run,
        "stage": result.get("stage"),
        "draft_id": result.get("draftId"),
        "product_id": result.get("productId"),
        "skc_list": result.get("productSkcList"),
        "property_count": result.get("propertyCount") or 0,
        "request_size": result.get("size") or 0,
        "material_img_url": result.get("materialImgUrl") or form.get("materialImgUrl") or "",
        "form": form,
    }


def compress_images(user_id: int, urls: list) -> dict:
    """压缩图片（返回 compressMap: 原 url → 压缩后 url）。"""
    account = require_account(user_id)
    items = [str(u).strip() for u in (urls or []) if str(u).strip()]
    if not items:
        raise StoreError("需要图片地址", code="ARGS", status=400)
    data = _bridge(temu_bridge.product_compress_image, account, items)
    return {"compressMap": data.get("compressMap") or {}}


def crop_image(user_id: int, url: str, cut_config: dict) -> dict:
    """服务端裁剪：把 CDN 图按 cutConfig 裁成新图。"""
    account = require_account(user_id)
    if not url:
        raise StoreError("需要图片地址", code="ARGS", status=400)
    cfg = cut_config or {}
    if not cfg.get("width") or not cfg.get("height"):
        raise StoreError("需要 cutConfig.width / height", code="ARGS", status=400)
    return _bridge(temu_bridge.product_crop_image, account, url, cfg)


def validate_size_template(
    user_id: int,
    cat_id: int,
    ids: list,
    general_size_type: int = 0,
    size_values: Optional[list] = None,
) -> dict:
    """预检尺码模板：类目不符 / 类型不符 / 尺码覆盖不到（6000012 的三种成因）。"""
    account = require_account(user_id)
    if not cat_id:
        raise StoreError("需要类目 ID", code="ARGS", status=400)
    cleaned = [int(x) for x in (ids or []) if str(x).strip().isdigit()]
    if not cleaned:
        return {
            "ok": True, "bad": [], "typeBad": [], "sizeBad": [],
            "available": [], "suggestion": None, "reason": "",
        }
    values = [str(x).strip() for x in (size_values or []) if str(x).strip()]
    data = _bridge(
        temu_bridge.product_validate_size_template,
        account,
        cat_id,
        cleaned,
        general_size_type,
        values,
    )
    return data


def material_folders(user_id: int) -> dict:
    """素材文件夹树。"""
    account = require_account(user_id)
    data = _bridge(temu_bridge.product_folders, account)
    return {"root": data.get("root")}


def remove_material(user_id: int, img_ids=None, video_ids=None) -> dict:
    """删除素材（批量下架）。"""
    account = require_account(user_id)
    imgs = [int(x) for x in (img_ids or []) if str(x).strip().isdigit()]
    vids = [int(x) for x in (video_ids or []) if str(x).strip().isdigit()]
    if not imgs and not vids:
        raise StoreError("请选择要删除的素材", code="ARGS", status=400)
    if len(imgs) > 100 or len(vids) > 100:
        raise StoreError("单次最多删除 100 个", code="ARGS", status=400)
    data = _bridge(temu_bridge.product_remove_material, account, imgs, vids)
    if not data.get("ok"):
        raise StoreError(data.get("msg") or "删除失败", code="UPSTREAM", status=400)
    return data


def upload_video(user_id: int, content_base64: str, file_name: str = "upload.mp4") -> dict:
    """上传视频到素材中心（含分片、转码轮询），返回 videoUrl / coverUrl / vid。"""
    account = require_account(user_id)
    if not content_base64:
        raise StoreError("缺少视频内容", code="ARGS", status=400)
    result = _bridge(temu_bridge.product_upload_video, account, content_base64, file_name)
    if not result.get("videoUrl"):
        raise StoreError("视频上传后没有拿到可用的视频地址（可能仍在转码）", code="VIDEO_PENDING", status=400)
    return result


def upload_image(
    user_id: int,
    content_base64: str,
    file_name: str = "upload.jpg",
    crop_width: int = 0,
    crop_height: int = 0,
) -> dict:
    """上传图片到素材库。crop_width/crop_height > 0 时服务端上传即按该尺寸裁切。"""
    account = require_account(user_id)
    crop = (crop_width, crop_height) if crop_width > 0 and crop_height > 0 else None
    return _bridge(temu_bridge.product_upload, account, content_base64, file_name, crop)


# ==================== 批量创建 ====================
_BATCH_LOCK = threading.Lock()
_BATCH_STATE: dict[int, dict] = {}


def _batch_key(user_id: int) -> int:
    return user_id


def batch_status(user_id: int) -> dict:
    """当前用户的批量任务进度。

    内存优先（最快）；内存没有时从库恢复——后端重启后仍能看到上次的进度与失败原因。
    """
    key = _batch_key(user_id)
    with _BATCH_LOCK:
        hit = _BATCH_STATE.get(key)
        if hit:
            return dict(hit)
    restored = load_batch_state(key)
    if not restored:
        return {"status": "idle"}
    # 进程重启时仍在「running」的任务其实已经中止了，标成 interrupted，
    # 否则前端会一直转圈等一个永远不会完成的批次
    if restored.get("status") == "running":
        restored["status"] = "interrupted"
        restored["current"] = ""
        save_batch_state(key, restored)
    with _BATCH_LOCK:
        _BATCH_STATE[key] = restored
    return dict(restored)


def start_batch(user_id: int, items: list, *, dry_run: bool = True) -> dict:
    """启动批量创建。items 每项是一条 normalize_form 前的原始表单。"""
    if not isinstance(items, list) or not items:
        raise StoreError("批量列表为空", code="ARGS", status=400)
    if len(items) > MAX_BATCH_ITEMS:
        raise StoreError(f"单批最多 {MAX_BATCH_ITEMS} 个商品", code="ARGS", status=400)

    with _BATCH_LOCK:
        current = _BATCH_STATE.get(_batch_key(user_id))
        if current and current.get("status") == "running":
            raise StoreError("已有批量任务在执行中，请等待完成", code="BUSY", status=409)
        _BATCH_STATE[_batch_key(user_id)] = {
            "status": "running",
            "total": len(items),
            "done": 0,
            "succeeded": 0,
            "failed": 0,
            "dry_run": dry_run,
            "current": "",
            "items": [
                {"index": i, "name": str(x.get("name") or ""), "status": "pending", "error": "", "product_id": None}
                for i, x in enumerate(items)
            ],
            "started_at": time.time(),
            "finished_at": None,
        }
        # 立刻落库：后端重启后还能看到「有一条跑着的批次」
        save_batch_state(_batch_key(user_id), _BATCH_STATE[_batch_key(user_id)])

    def _run():
        state = _BATCH_STATE[_batch_key(user_id)]
        for i, raw in enumerate(items):
            if state.get("cancelled"):
                for row in state["items"][i:]:
                    row["status"] = "cancelled"
                break
            state["current"] = str(raw.get("name") or f"第 {i + 1} 条")
            try:
                result = create(user_id, raw, dry_run=dry_run, batch=True)
                state["items"][i].update({"status": "ok", "product_id": result.get("product_id")})
                state["succeeded"] += 1
            except StoreError as exc:
                state["items"][i].update({"status": "failed", "error": exc.message})
                state["failed"] += 1
            except Exception as exc:  # noqa: BLE001 - 单条失败不能拖垮整批
                logger.exception("批量创建第 %s 条失败", i + 1)
                state["items"][i].update({"status": "failed", "error": str(exc)[:200]})
                state["failed"] += 1
            state["done"] = i + 1
            save_batch_state(_batch_key(user_id), state)
            if i < len(items) - 1:
                # 平台限流：一个商品至少 3 个请求，必须留出间隔
                time.sleep(ITEM_INTERVAL_SECONDS)
        state["status"] = "done"
        state["current"] = ""
        state["finished_at"] = time.time()
        save_batch_state(_batch_key(user_id), state)

    threading.Thread(target=_run, name=f"product-create-{user_id}", daemon=True).start()
    return batch_status(user_id)


def cancel_batch(user_id: int) -> dict:
    with _BATCH_LOCK:
        state = _BATCH_STATE.get(_batch_key(user_id))
        if state and state.get("status") == "running":
            state["cancelled"] = True
    return batch_status(user_id)
