from __future__ import annotations

import json
import logging
import urllib.request
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from openpyxl import load_workbook

from app.config import (
    AUTH_SECRET_IS_DEFAULT,
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    CLEANUP_INTERVAL_SECONDS,
    COOKIE_SECURE,
    INVENTORY_DIR,
    INVENTORY_PATH,
    MAX_UPLOAD_BYTES,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    ensure_directories,
)
from app.database import (
    get_temu_session,
    create_user,
    database_status,
    create_inventory_item,
    delete_inventory_item,
    update_inventory_item,
    delete_user,
    ensure_admin_user,
    get_user,
    get_user_by_username,
    get_temu_credential,
    list_users,
    delete_audit_logs,
    delete_kefu_actions,
    delete_temu_login_logs,
    list_temu_login_logs,
    update_user_credentials,
    update_user_status,
    bulk_delete_users,
    bulk_update_users,
    count_half_entries,
    create_inventory_category_def,
    list_inventory_category_defs,
    update_inventory_category_def,
    delete_inventory_category_def,
    save_activity_skc_rules,
)
from app.schemas import ActivitySkuRulesPayload, AdminUserBulkDeleteRequest, AdminUserBulkUpdateRequest, AdminUserUpdateRequest, CategoryCreateRequest, CategoryUpdateRequest, HalfHeadcostCreateRequest, HalfHeadcostUpdateRequest, InventoryApplyRequest, InventoryCategoryCreateRequest, InventoryCategoryUpdateRequest, InventoryItemCreateRequest, InventoryItemUpdateRequest, LoginRequest, RegionCreateRequest, RegionUpdateRequest, RegisterRequest, SettingsPayload, SkuQueryRequest, StoreKefuActionRequest, StoreKefuBatchRequest, StoreKefuQueryBatchRequest, StoreKefuQueryRequest, StoreLoginRequest, SystemSettingsPayload, WeightHeadcostCreateRequest, WeightHeadcostUpdateRequest
from app.services.auth import admin_user, current_user, hash_password, login_user, make_session, public_user, store_user, validate_username
from app.services import audit, permissions, credential_cipher, product_create, product_templates, store_accounts, store_goods, store_kefu, store_kefu_actions, store_kefu_batch, store_kefu_retry, store_session, system, updater
from app.services.store_session import StoreError
from app.services.categories import category_allowed_pieces, category_skc_rules, create_category, delete_category, get_category, list_categories, TEMPLATE_TYPES, update_category
from app.services.cleanup import cleanup_scheduler, purge_all, run_once as run_cleanup_once
from app.services.half_headcost import delete_entry, entry_exists, load_entries, merge_upload, save_entry
from app.services.weight_headcost import delete_entry as delete_weight_entry, entry_exists as weight_entry_exists, headcost_for_weight, load_entries as load_weight_entries, merge_upload as merge_weight_upload, save_entry as save_weight_entry
from app.services.activity import MAX_PREVIEW_PAGE_SIZE, MAX_PREVIEW_ROWS, PREVIEW_RESULT_FILTERS, normalize_drop_skc_list, normalize_id_profit_rules, normalize_parse_config, preview_activity_workbook
from app.services.activity_tasks import activity_task_manager
from app.services.monitoring import snapshot as monitoring_snapshot
from app.services.regions import create_region, delete_region, get_region_profile, list_regions, region_snapshot, update_region
from app.services.inventory import all_pending_uploads, apply_pending_upload, discard_pending_upload, filter_catalog_items, inventory_categories, inventory_path_for, invalidate_cache, inventory_status, load_price_catalog, pending_upload, resolve_profile, stage_pending_upload
from app.services.taskpool import QueueFullError
from app.services.tasks import task_manager
from app.version import info as version_info, version as app_version


ensure_directories()
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger("temubox.store")

if AUTH_SECRET_IS_DEFAULT:
    logger.warning(
        "AUTH_SECRET 仍是默认值，会话 cookie 可被伪造；请在 .env 或环境变量里设置一个随机密钥"
    )

app = FastAPI(
    title="Temu-Box API",
    version=app_version(),
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== 模块权限守卫 ====================
# 前端隐藏菜单只挡住普通操作；这里才是真正的边界：
# 没有对应模块权限时，直接调 API 也返回 403。
_STORE_WRITE_HINTS = ("/create", "/preview", "/batch", "/upload", "/material")


def _module_rule(path: str, method: str) -> Optional[tuple[str, Optional[str]]]:
    """路径 → (一级模块, 店铺二级模块)。None 表示不拦。"""
    if not path.startswith("/api/"):
        return None
    # 公共接口：登录、健康检查、自身信息、模块清单
    if path.startswith(("/api/auth/", "/api/health", "/api/permissions", "/api/version", "/api/status", "/api/categories")):
        return None
    if path.startswith("/api/admin/"):
        return None  # 由 admin_user 依赖负责
    if path.startswith("/api/store/"):
        sub = path[len("/api/store/"):]
        if sub.startswith("kefu"):
            return ("store", "kefu")
        if sub.startswith("actions"):
            return ("store", "actions")
        if sub.startswith("goods"):
            return ("store", "goods")
        if sub.startswith("products"):
            # 查询/模板/素材走 goods，创建/预览/批量/上传走 create
            if method in ("POST", "PUT", "PATCH", "DELETE") or any(h in path for h in _STORE_WRITE_HINTS):
                return ("store", "create")
            return ("store", "goods")
        return ("store", None)
    if path.startswith("/api/inventory"):
        return ("inventory", None)
    if path.startswith("/api/activities"):
        return ("activities", None)
    if path.startswith("/api/tasks"):
        return ("tasks", None)
    return None


@app.middleware("http")
async def module_guard(request: Request, call_next):
    rule = _module_rule(request.url.path, request.method)
    if rule is None:
        return await call_next(request)

    try:
        user = current_user(request)
    except HTTPException:
        # 未登录交给原接口返回 401，这里不抢答
        return await call_next(request)

    module, sub_module = rule
    if not permissions.has_module(user, module):
        return JSONResponse(
            status_code=403,
            content={"detail": f"没有「{permissions.label_of(module)}」模块权限，请联系管理员开通"},
        )
    if sub_module and not permissions.has_store_module(user, sub_module):
        return JSONResponse(
            status_code=403,
            content={"detail": f"没有店铺管理里的「{permissions.label_of(sub_module)}」权限，请联系管理员开通"},
        )
    return await call_next(request)



@app.on_event("startup")
def bootstrap_admin() -> None:
    if ADMIN_PASSWORD:
        ensure_admin_user(ADMIN_USERNAME, hash_password(ADMIN_PASSWORD))
    elif not get_user_by_username(ADMIN_USERNAME):
        logging.getLogger("temubox.auth").warning("ADMIN_PASSWORD 未配置，管理员账号尚未创建")
    cleanup_scheduler.start()
    # 线程只能 start 一次：多个 TestClient / 多次 startup 时保持幂等
    if not store_kefu_retry.scheduler.is_alive():
        store_kefu_retry.scheduler.start()


def resolve_inventory_category(category: Optional[str]) -> str:
    """解析库存类目键；类目不存在/已停用时返回 400，而不是静默回退到 A。"""
    from app.services.inventory import resolve_profile
    try:
        return resolve_profile(category or "A")["key"]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def validate_excel(upload: UploadFile) -> None:
    filename = upload.filename or ""
    if not filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail=f"{filename or '文件'} 不是 .xlsx/.xlsm 文件")


async def save_upload(upload: UploadFile, destination: Path) -> int:
    validate_excel(upload)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(destination.suffix + ".upload")
    size = 0
    try:
        with temp.open("wb") as handle:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="上传文件超过服务器限制")
                handle.write(chunk)
        os.replace(temp, destination)
    finally:
        await upload.close()
        if temp.exists():
            temp.unlink()
    return size


@app.get("/api/health")
def health():
    database = database_status()
    return {"status": "ok" if database["status"] == "ok" else "degraded", "version": app.version, "database": database}


@app.post("/api/auth/register")
def register(request: RegisterRequest, http_request: Request):
    try:
        username = validate_username(request.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if get_user_by_username(username):
        raise HTTPException(status_code=409, detail="用户名已存在")
    try:
        create_user(username, hash_password(request.password), request.display_name.strip())
    except Exception as exc:
        if get_user_by_username(username):
            raise HTTPException(status_code=409, detail="用户名已存在") from exc
        raise
    audit.record(None, "auth.register", "user", username, "新用户注册，等待审核", http_request)
    return {"message": "注册成功，请等待管理员审核"}


@app.post("/api/auth/login")
def login(request: LoginRequest, http_request: Request, response: Response):
    try:
        user = login_user(request.username, request.password)
    except HTTPException as exc:
        audit.record(None, "auth.login_failed", "user", request.username, exc.detail, http_request)
        raise
    response.set_cookie(SESSION_COOKIE_NAME, make_session(user["id"]), max_age=SESSION_MAX_AGE, httponly=True, samesite="lax", secure=COOKIE_SECURE)
    audit.record(user, "auth.login", "user", user["username"], "登录成功", http_request)
    return public_user(user)


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"message": "已退出登录"}


@app.get("/api/auth/me")
def me(user: dict = Depends(current_user)):
    return {**public_user(user), **permissions.apply_to_form(user)}


@app.get("/api/status")
def status(user: dict = Depends(current_user)):
    half_count = count_half_entries()
    return {
        "version": app.version,
        "inventory": inventory_status(),
        "half_headcost_count": half_count,
        "tasks": task_manager.list(8, user["id"]),
    }


@app.get("/api/categories")
def public_categories(region_code: Optional[str] = Query(None, max_length=16), _user: dict = Depends(current_user)):
    return {"items": list_categories(region_code=region_code)}


# ==================== 店铺管理（每个用户登录自己的店铺） ====================

def _store_http_error(exc: StoreError) -> HTTPException:
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    # 记下具体原因，400 只回给前端 detail，日志里看不到会很难排查
    logger.warning("store error [%s/%s]: %s", exc.code or "-", exc.status, exc.message)
    return HTTPException(status_code=exc.status, detail=exc.message, headers=headers)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


@app.get("/api/store/session")
def store_session_view(user: dict = Depends(store_user)):
    return store_session.view(user["id"])


@app.post("/api/store/session/send-code")
def store_session_send_code(payload: dict, user: dict = Depends(store_user)):
    """发登录验证码。触发风控（54001）时前端用来弹「获取验证码」。"""
    account = str(payload.get("account") or "").strip()
    if not account:
        session = get_temu_session(user["id"]) or {}
        account = str(session.get("account") or "")
    if not account:
        raise HTTPException(status_code=400, detail="请先填写账号")
    try:
        return temu_bridge.send_login_code(account, payload.get("contact_type"))
    except StoreError as exc:
        raise _store_http_error(exc) from None
    except Exception as exc:  # noqa: BLE001 - bridge 侧错误统一转 502
        raise HTTPException(status_code=502, detail=f"发码失败：{exc}") from None


@app.post("/api/store/session/login")
def store_session_login(payload: StoreLoginRequest, request: Request, user: dict = Depends(store_user)):
    try:
        data = store_session.login(
            user,
            account=payload.account or "",
            password=payload.password or "",
            mall_id=payload.mall_id,
            remember=payload.remember,
            use_remembered=payload.use_remembered,
            account_id=payload.account_id,
            verify_code=payload.verify_code,
            ip=_client_ip(request),
        )
    except StoreError as exc:
        audit.record(
            user,
            "store.login_failed",
            "temu_session",
            store_session.mask_account(payload.account or ""),
            exc.message,
            request,
        )
        raise _store_http_error(exc) from None
    # 新会话的卡片 msgId 与旧会话无关，清掉缓存重新唤醒
    store_kefu.invalidate_card_cache()
    store_goods.invalidate_cache()
    audit.record(
        user,
        "store.login",
        "temu_session",
        data.get("account_masked", ""),
        f"登录店铺：{data.get('mall_name') or ''}",
        request,
    )
    return data


@app.post("/api/store/session/logout")
def store_session_logout(request: Request, user: dict = Depends(store_user)):
    data = store_session.logout(user, ip=_client_ip(request))
    # 换会话/退出后旧卡片 msgId 不可再用
    store_kefu.invalidate_card_cache()
    store_goods.invalidate_cache()
    audit.record(user, "store.logout", "temu_session", data.get("account_masked", ""), "退出店铺登录", request)
    return data


@app.get("/api/store/accounts")
def store_list_accounts(user: dict = Depends(store_user)):
    """当前用户保存的店铺账号列表（不含密码）。"""
    try:
        return {"items": store_accounts.list_for_user(user["id"])}
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.post("/api/store/accounts")
async def store_add_account(payload: dict, request: Request, user: dict = Depends(store_user)):
    """新增/更新一个店铺账号密码（默认调用方已勾选保存）。"""
    try:
        item = store_accounts.encrypt_and_save(
            user["id"],
            account=str(payload.get("account") or ""),
            password=str(payload.get("password") or ""),
            label=str(payload.get("label") or ""),
            mall_name=str(payload.get("mall_name") or ""),
        )
    except StoreError as exc:
        raise _store_http_error(exc) from None
    audit.record(user, "store.account_save", "temu_store_account", item["account_masked"], "保存店铺账号", request)
    return item


@app.delete("/api/store/accounts/{account_id}")
def store_delete_account(account_id: int, request: Request, user: dict = Depends(store_user)):
    try:
        ok = store_accounts.remove(user["id"], account_id)
    except StoreError as exc:
        raise _store_http_error(exc) from None
    if not ok:
        raise HTTPException(status_code=404, detail="店铺账号不存在")
    audit.record(user, "store.account_delete", "temu_store_account", str(account_id), "删除店铺账号", request)
    return {"deleted": True}


@app.post("/api/store/session/refresh")
def store_session_refresh(request: Request, user: dict = Depends(store_user)):
    return store_session.refresh(user, ip=_client_ip(request))


@app.delete("/api/store/session/remember")
def store_session_forget_password(request: Request, user: dict = Depends(store_user)):
    cleared = store_session.clear_remembered(user["id"])
    audit.record(user, "store.forget_password", "temu_session", user["username"], "清除已保存的店铺密码", request)
    return {"cleared": bool(cleared)}


@app.get("/api/store/sites")
def store_sites(force: bool = Query(False), user: dict = Depends(store_user)):
    """104 个站点表（默认带缓存，force=true 强制刷新）。"""
    try:
        return {"items": store_goods.sites(user["id"], force=force)}
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.get("/api/store/goods")
def store_goods_list(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    site: Optional[str] = Query(None, max_length=32),
    keyword: Optional[str] = Query(None, max_length=120),
    # 0 = 未发布到任何站点，1 = 已加入站点（由生命周期 selectStatus=12 推导）
    site_status: Optional[int] = Query(None, ge=0, le=1),
    # 生命周期状态：9 待选品 / 10 待处理 / 12 已加入站点 / 13 待价格校验
    select_status: Optional[int] = Query(None),
    # 搜索类型：auto（数字→SKC，其它→商品名）/ name / spu / skc / sku / extcode
    search_type: Optional[str] = Query(None, max_length=16),
    # 时间范围（1 创建 / 4 价格确认 / 6 加入站点 / 7 下架）
    time_type: Optional[int] = Query(None),
    time_begin: Optional[int] = Query(None),
    time_end: Optional[int] = Query(None),
    # 状态类筛选：逗号分隔的枚举列表
    price_review_status: Optional[str] = Query(None, max_length=64),
    sample_qc_status: Optional[str] = Query(None, max_length=64),
    todo_types: Optional[str] = Query(None, max_length=64),
    punish_status: Optional[str] = Query(None, max_length=64),
    limit_status: Optional[str] = Query(None, max_length=64),
    remove_status: Optional[int] = Query(None),
    force: bool = Query(False),
    user: dict = Depends(store_user),
):
    """商品列表（行粒度 = SKC，数据来自「上新生命周期管理」接口并走服务端筛选）。

    一次只取一页（平台连续 8~10 次请求/分钟会 40001），异常时回退商品列表接口。
    """
    def _ints(raw: Optional[str]) -> Optional[list[int]]:
        if not raw:
            return None
        values = [part.strip() for part in raw.split(",") if part.strip()]
        return [int(v) for v in values if v.lstrip("-").isdigit()] or None

    try:
        return store_goods.list_goods(
            user["id"],
            page=page,
            page_size=page_size,
            site_id=site,
            keyword=keyword,
            search_type=search_type,
            site_status=site_status,
            select_status=select_status,
            time_type=time_type,
            time_begin=time_begin,
            time_end=time_end,
            price_review_status=_ints(price_review_status),
            sample_qc_status=_ints(sample_qc_status),
            todo_types=_ints(todo_types),
            punish_status=_ints(punish_status),
            limit_status=_ints(limit_status),
            remove_status=remove_status,
            force=force,
        )
    except StoreError as exc:
        raise _store_http_error(exc) from None


# ==================== 商品创建 ====================
@app.post("/api/store/products/category/suggest")
def store_product_category_suggest(payload: dict, user: dict = Depends(store_user)):
    """按商品名推荐 Temu 类目路径（填 catPath 用）。"""
    try:
        return product_create.category_suggest(user["id"], str(payload.get("name") or ""))
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.get("/api/store/products/constants")
def store_product_constants(user: dict = Depends(store_user)):
    """固定枚举：敏感类型 / SKU 分类。"""
    try:
        return product_create.constants(user["id"])
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.get("/api/store/products/regions")
def store_product_regions(refresh: bool = Query(False), user: dict = Depends(store_user)):
    """产地 / 制造地点候选值。"""
    try:
        return product_create.regions(user["id"], refresh=refresh)
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.get("/api/store/products/size-templates")
def store_product_size_templates(
    cat_id: int = Query(..., ge=1),
    site_id: int = Query(0),
    group_id: int = Query(0),
    user: dict = Depends(store_user),
):
    """尺码表模板列表（businessId → sizeTemplateIds）。按站点尺码组过滤。"""
    try:
        return product_create.size_templates(user["id"], cat_id, site_id, group_id)
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.get("/api/store/products/size-spec-config")
def store_product_size_spec_config(cat_id: int = Query(..., ge=1), user: dict = Depends(store_user)):
    """尺码体系映射（站点 → 尺码组）。"""
    try:
        return product_create.size_spec_config(user["id"], cat_id)
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.get("/api/store/products/category/config")
def store_product_category_config(
    leaf_cat_id: int = Query(..., ge=1),
    site_id: int = Query(100, ge=1),
    user: dict = Depends(store_user),
):
    """类目配置：必填项、标题长度上限、敏感属性。"""
    try:
        return product_create.category_config(user["id"], leaf_cat_id, site_id)
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.post("/api/store/products/category/search")
def store_product_category_search(payload: dict, user: dict = Depends(store_user)):
    """类目关键字搜索（多条模糊）。"""
    try:
        return product_create.category_search(
            user["id"],
            str(payload.get("keyword") or ""),
            is_en=bool(payload.get("is_en")),
        )
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.get("/api/store/products/category/hints")
def store_product_category_hints(user: dict = Depends(store_user)):
    """常用推荐类目。"""
    try:
        return product_create.category_hints(user["id"])
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.get("/api/store/products/warehouses")
def store_product_warehouses(site_id: Optional[int] = Query(None), user: dict = Depends(store_user)):
    """发货仓列表（含名称与覆盖站点）。"""
    try:
        return product_create.warehouses(user["id"], site_id)
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.get("/api/store/products/category/children")
def store_product_category_children(
    parent_cat_id: int = Query(0, ge=0),
    user: dict = Depends(store_user),
):
    """逐级类目树（parent_cat_id=0 取一级）。"""
    try:
        return product_create.category_children(user["id"], parent_cat_id)
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.post("/api/store/products/category/path")
def store_product_category_path(payload: dict, user: dict = Depends(store_user)):
    """catId 列表反查完整类目路径。"""
    try:
        return product_create.category_path(user["id"], payload.get("cat_ids") or [])
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.get("/api/store/products/template")
def store_product_template(
    cat_id: int = Query(..., ge=1),
    refresh: bool = Query(False),
    user: dict = Depends(store_user),
):
    """类目模板（属性定义 + 概要），前端据此渲染表单。"""
    try:
        return product_create.category_template(user["id"], cat_id, refresh=refresh)
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.post("/api/store/products/describe")
def store_product_describe(payload: dict, user: dict = Depends(store_user)):
    """按已选属性返回应显示字段 / 缺失项 / 颜色尺码选项。"""
    try:
        return product_create.describe(user["id"], int(payload.get("cat_id") or 0), payload.get("selected") or {})
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.get("/api/store/products/sites")
def store_product_sites(refresh: bool = Query(False), user: dict = Depends(store_user)):
    try:
        return product_create.sites(user["id"], refresh=refresh)
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.get("/api/store/products/env")
def store_product_env(
    site_id: Optional[int] = Query(None),
    refresh: bool = Query(False),
    user: dict = Depends(store_user),
):
    """店铺环境：可用仓库 + 运费模板（创建商品必需）。"""
    try:
        return product_create.store_env(user["id"], site_id, refresh=refresh)
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.get("/api/store/products/size-template/validate")
def store_product_validate_size_template(
    cat_id: int = Query(..., ge=1),
    ids: str = Query(""),
    general_size_type: int = Query(0),
    size_values: str = Query(""),
    user: dict = Depends(store_user),
):
    """预检尺码模板：类目不符 / 类型不符 / 尺码覆盖不到。"""
    try:
        return product_create.validate_size_template(
            user["id"],
            cat_id,
            [x for x in ids.split(",") if x.strip()],
            general_size_type,
            [x for x in size_values.split(",") if x.strip()],
        )
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.get("/api/store/products/image-proxy")
def store_product_image_proxy(url: str = Query(..., max_length=2000), user: dict = Depends(store_user)):
    """同源转发素材图，供前端 canvas 裁剪使用。

    浏览器的 canvas 一旦画出跨域图就会被污染，toDataURL 直接抛 SecurityError；
    而给 <img> 加 crossOrigin 又要求 CDN 返回正确的 CORS 头，缺一个头整张图都加载不出来。
    走同源代理一劳永逸：前端拿到的就是自家域名下的字节。

    ⚠️ 只允许 Temu 的图片域名，避免变成任意 URL 的 SSRF 跳板。
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    allowed_suffix = (".kwcdn.com", ".temu.com", ".pddpic.com", ".alicdn.com")
    if parsed.scheme not in ("http", "https") or not host.endswith(allowed_suffix):
        raise HTTPException(status_code=400, detail="不允许的图片地址")

    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://seller.kuajingmaihuo.com/"})
    try:
        with urllib.request.urlopen(request, timeout=20) as upstream:
            data = upstream.read(12 * 1024 * 1024 + 1)  # 超过 12MB 视为异常
            content_type = upstream.headers.get("Content-Type", "image/jpeg")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"获取图片失败：{exc}") from None

    if len(data) > 12 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="图片过大")
    return Response(content=data, media_type=content_type)


@app.get("/api/store/products/form-templates")
def store_form_templates(user: dict = Depends(store_user)):
    """我的表单模板列表（只返回当前店铺的）。"""
    try:
        session = get_temu_session(user["id"]) or {}
        return product_templates.list_templates(user["id"], shop=str(session.get("account") or ""))
    except Exception as exc:  # noqa: BLE001 - 不能把 500 扔给前端而没有任何线索
        logger.exception("列表单模板失败")
        raise HTTPException(status_code=500, detail=f"列表单模板失败：{exc}") from None


@app.get("/api/store/products/form-templates/{template_id}")
def store_form_template_detail(template_id: int, user: dict = Depends(store_user)):
    """取模板内容（只返回可复用字段）。"""
    try:
        session = get_temu_session(user["id"]) or {}
        return product_templates.get_template(
            user["id"], template_id, shop=str(session.get("account") or "")
        )
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.post("/api/store/products/form-templates")
def store_form_template_save(payload: dict, user: dict = Depends(store_user)):
    """把当前填好的表单存成模板（同名则覆盖）。"""
    try:
        session = get_temu_session(user["id"]) or {}
        return product_templates.save_template(
            user["id"],
            str(payload.get("name") or ""),
            str(payload.get("note") or ""),
            payload.get("form") or {},
            shop=str(session.get("account") or ""),
        )
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.patch("/api/store/products/form-templates/{template_id}")
def store_form_template_update(template_id: int, payload: dict, user: dict = Depends(store_user)):
    """改模板名称/备注；传 form 时同时用新参数覆盖内容。"""
    try:
        return product_templates.update_template(
            user["id"],
            template_id,
            name=payload.get("name"),
            note=payload.get("note"),
            form=payload.get("form"),
        )
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.delete("/api/store/products/form-templates/{template_id}")
def store_form_template_delete(template_id: int, user: dict = Depends(store_user)):
    try:
        return product_templates.delete_template(user["id"], template_id)
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.get("/api/store/products/folders")
def store_product_folders(user: dict = Depends(store_user)):
    """素材文件夹树。"""
    try:
        return product_create.material_folders(user["id"])
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.post("/api/store/products/material/remove")
def store_product_material_remove(payload: dict, user: dict = Depends(store_user)):
    """删除素材（批量下架）。"""
    try:
        return product_create.remove_material(
            user["id"],
            payload.get("img_ids") or [],
            payload.get("video_ids") or [],
        )
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.get("/api/store/products/images")
def store_product_images(
    carousel: bool = Query(False),
    page_no: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    name: Optional[str] = Query(None, max_length=80),
    proportion: Optional[str] = Query(None, max_length=16),
    # 0=视频 1=图片，不传=图片
    type: Optional[int] = Query(None, ge=0, le=1),
    folder_id: int = Query(0, ge=0),
    user: dict = Depends(store_user),
):
    """素材库（创建商品只能从素材库取图/视频）。"""
    try:
        return product_create.images(
            user["id"],
            carousel=carousel,
            page_no=page_no,
            page_size=page_size,
            name=name,
            proportion=proportion,
            material_type=type,
            folder_id=folder_id,
        )
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.post("/api/store/products/preview")
def store_product_preview(payload: dict, user: dict = Depends(store_user)):
    """校验表单 + 组装请求体（不创建）。"""
    try:
        return product_create.preview(user["id"], payload.get("form") or payload)
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.post("/api/store/products/create")
def store_product_create(payload: dict, request: Request, user: dict = Depends(store_user)):
    """创建商品。dry_run 默认 True（只到 draft/save）。"""
    dry_run = payload.get("dry_run")
    dry_run = True if dry_run is None else bool(dry_run)
    try:
        result = product_create.create(user["id"], payload.get("form") or payload, dry_run=dry_run)
    except StoreError as exc:
        audit.record(user, "store.product_create_failed", "temu_product", str(payload.get("form", {}).get("name", ""))[:80], exc.message, request)
        raise _store_http_error(exc) from None
    audit.record(
        user,
        "store.product_create",
        "temu_product",
        str(result.get("product_id") or result.get("draft_id") or ""),
        f"{'预览' if dry_run else '创建'}：{result.get('form', {}).get('name', '')}",
        request,
    )
    return result


@app.post("/api/store/products/material/compress")
def store_product_material_compress(payload: dict, user: dict = Depends(store_user)):
    """压缩图片（超尺寸时用）。"""
    try:
        return product_create.compress_images(user["id"], payload.get("urls") or [])
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.post("/api/store/products/material/crop")
def store_product_material_crop(payload: dict, user: dict = Depends(store_user)):
    """服务端裁剪：把 CDN 图按 cutConfig 裁成新图。"""
    try:
        return product_create.crop_image(
            user["id"], str(payload.get("url") or ""), payload.get("cut_config") or {}
        )
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.post("/api/store/products/upload-video")
def store_product_upload_video(payload: dict, user: dict = Depends(store_user)):
    """上传视频到素材中心（≤30MB，MP4，内部会等转码完成）。"""
    try:
        return product_create.upload_video(
            user["id"],
            str(payload.get("content_base64") or ""),
            str(payload.get("file_name") or "upload.mp4"),
        )
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.post("/api/store/products/upload-many")
def store_product_upload_many(payload: dict, user: dict = Depends(store_user)):
    """批量上传图片到素材库（对齐官网 5 步流程，含 MD5 查重与并发上传）。"""
    try:
        return product_create.upload_images(
            user["id"],
            payload.get("files") or [],
            int(payload.get("concurrency") or 4),
        )
    except StoreError as exc:
        raise _store_http_error(exc) from None
    except Exception as exc:  # noqa: BLE001 - 批量上传涉及上游多步，兜住未预期的异常避免裸 500
        logging.getLogger(__name__).exception("批量上传失败")
        raise HTTPException(status_code=502, detail=f"批量上传失败：{exc}") from None


@app.post("/api/store/products/upload")
def store_product_upload(payload: dict, user: dict = Depends(store_user)):
    """上传图片到素材库（base64，≤2MB）。"""
    try:
        return product_create.upload_image(
            user["id"],
            str(payload.get("content_base64") or ""),
            str(payload.get("file_name") or "upload.jpg"),
            int(payload.get("crop_width") or 0),
            int(payload.get("crop_height") or 0),
        )
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.get("/api/store/products/batch")
def store_product_batch_status(user: dict = Depends(store_user)):
    return product_create.batch_status(user["id"])


@app.post("/api/store/products/batch")
def store_product_batch_start(payload: dict, request: Request, user: dict = Depends(store_user)):
    """启动批量创建（后台串行执行，每项间隔 20 秒）。"""
    dry_run = payload.get("dry_run")
    dry_run = True if dry_run is None else bool(dry_run)
    try:
        state = product_create.start_batch(user["id"], payload.get("items") or [], dry_run=dry_run)
    except StoreError as exc:
        raise _store_http_error(exc) from None
    audit.record(
        user,
        "store.product_batch_start",
        "temu_product",
        str(state.get("total")),
        f"批量{'预览' if dry_run else '创建'} {state.get('total')} 个商品",
        request,
    )
    return state


@app.post("/api/store/products/batch/cancel")
def store_product_batch_cancel(user: dict = Depends(store_user)):
    return product_create.cancel_batch(user["id"])


@app.get("/api/store/goods/filters")
def store_goods_filters(user: dict = Depends(store_user)):
    """生命周期快捷筛选计数 + 待办计数（给筛选下拉显示数量）。"""
    try:
        return store_goods.quick_filters(user["id"])
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.get("/api/store/goods/{product_id}/lifecycle")
def store_goods_lifecycle(product_id: str, user: dict = Depends(store_user)):
    """该商品各 SKC 的生命周期阶段与时间轴（详情抽屉用）。"""
    try:
        return store_goods.product_lifecycle(user["id"], product_id)
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.get("/api/store/goods/{product_id}")
def store_goods_detail(product_id: str, user: dict = Depends(store_user)):
    """商品详情（queryForImage：轮播图 / 多语言 / SKC-SKU 结构）。"""
    try:
        return store_goods.goods_detail(user["id"], product_id)
    except StoreError as exc:
        raise _store_http_error(exc) from None


# ==================== 客服自助工具（只读；写操作在 P3 的 action 事务里） ====================

@app.get("/api/store/kefu/tools")
def store_kefu_tools(force: bool = Query(False), user: dict = Depends(store_user)):
    """4 个自助工具定义（toolId 由服务端下发，默认缓存 30 分钟）。"""
    try:
        return {"items": store_kefu.tools(user["id"], force=force)}
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.get("/api/store/kefu/card")
def store_kefu_card(
    tool_id: Optional[str] = Query(None, max_length=32),
    max_pages: int = Query(10, ge=1, le=50),
    user: dict = Depends(store_user),
):
    """工具卡片（下架原因枚举的来源）。会按 toolId 精确找并自动向前翻页。"""
    try:
        return store_kefu.card(user["id"], tool_id=tool_id, max_pages=max_pages)
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.get("/api/store/kefu/cards")
def store_kefu_cards(
    max_pages: int = Query(10, ge=1, le=50),
    user: dict = Depends(store_user),
):
    """每个工具的最新卡片（可用于预热 parentMsgId / 排障）。"""
    try:
        return {"items": store_kefu.cards(user["id"], max_pages=max_pages)}
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.get("/api/store/kefu/messages")
def store_kefu_messages(limit: int = Query(50, ge=1, le=50), user: dict = Depends(store_user)):
    """客服会话消息（服务端上限 50 条，已剥离 HTML）。"""
    try:
        return store_kefu.messages(user["id"], limit=limit)
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.post("/api/store/kefu/query")
def store_kefu_query(payload: StoreKefuQueryRequest, user: dict = Depends(store_user)):
    """只读查商品（SKC / SKU / SPU），提交前确认商品身份用。"""
    try:
        return store_kefu.query_product(user["id"], payload.type, payload.id, payload.tool_id)
    except StoreError as exc:
        raise _store_http_error(exc) from None


# ==================== 客服写操作：预览 → 确认 → 回执 ====================

@app.post("/api/store/kefu/query-batch")
def store_kefu_query_batch(payload: StoreKefuQueryBatchRequest, user: dict = Depends(store_user)):
    """批量只读查询：一串编号换成商品详情（可反复执行，不产生任何副作用）。"""
    try:
        return store_kefu.query_batch(user["id"], payload.type, payload.ids, payload.tool_id)
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.post("/api/store/kefu/batches", status_code=202)
def store_kefu_batch_create(payload: StoreKefuBatchRequest, request: Request, user: dict = Depends(store_user)):
    """批量执行客服工具：入队后在后台线程串行「查商品 → 拦截 → 提交 → 回执」。"""
    try:
        data = store_kefu_batch.start_batch(
            user,
            tool_id=payload.tool_id or "",
            tool_name=payload.tool_name or "",
            data_type=payload.data_type or "",
            ids=payload.ids,
            reason_code=payload.reason_code,
            reason_detail=payload.reason_detail or "",
        )
    except StoreError as exc:
        audit.record(user, "store.kefu_batch_failed", "temu_kefu_batch", payload.tool_id or "", exc.message, request)
        raise _store_http_error(exc) from None
    batch = data["batch"]
    audit.record(
        user,
        "store.kefu_batch",
        "temu_kefu_batch",
        batch["id"],
        f"{batch['tool_name']} · {batch['data_type_name']} · {batch['total']} 个编号",
        request,
    )
    return data


@app.get("/api/store/kefu/batches")
def store_kefu_batch_list(limit: int = Query(20, ge=1, le=50), user: dict = Depends(store_user)):
    return {"items": store_kefu_batch.list_batches(user, limit=limit)}


@app.get("/api/store/kefu/batches/{batch_id}")
def store_kefu_batch_get(batch_id: str, user: dict = Depends(store_user)):
    """批量进度：整批状态 + 每个编号的阶段/回执。"""
    try:
        return store_kefu_batch.batch_view(user, batch_id)
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.post("/api/store/kefu/batches/{batch_id}/cancel")
def store_kefu_batch_cancel(batch_id: str, request: Request, user: dict = Depends(store_user)):
    """停止剩余未执行的编号（已发送的无法撤回）。"""
    try:
        data = store_kefu_batch.cancel_batch(user, batch_id)
    except StoreError as exc:
        raise _store_http_error(exc) from None
    audit.record(user, "store.kefu_batch_cancel", "temu_kefu_batch", batch_id, "取消批量任务", request)
    return data


@app.post("/api/store/kefu/actions", status_code=201)
def store_kefu_action_create(payload: StoreKefuActionRequest, request: Request, user: dict = Depends(store_user)):
    """创建写操作预览：一次完成查商品 + 拦截检查 + 组装报文（**不发送**）。"""
    try:
        action = store_kefu_actions.create_action(
            user,
            tool_id=payload.tool_id or "",
            tool_name=payload.tool_name or "",
            data_type=payload.data_type or "",
            data_id=payload.data_id,
            reason_code=payload.reason_code,
            reason_detail=payload.reason_detail or "",
            parent_msg_id=payload.parent_msg_id,
        )
    except StoreError as exc:
        audit.record(
            user,
            "store.kefu_preview_failed",
            "temu_kefu_action",
            payload.data_id,
            f"{payload.tool_id or payload.tool_name or ''}：{exc.message}",
            request,
        )
        raise _store_http_error(exc) from None
    audit.record(
        user,
        "store.kefu_preview",
        "temu_kefu_action",
        action["id"],
        f"{action['tool_name']} {action['data_type_name']} {action['data_id']}",
        request,
    )
    return action


@app.post("/api/store/kefu/actions/{action_id}/confirm")
def store_kefu_action_confirm(action_id: str, request: Request, user: dict = Depends(store_user)):
    """确认发送（幂等：同一 action 只会真正发一次消息）。"""
    try:
        action = store_kefu_actions.confirm_action(user, action_id)
    except StoreError as exc:
        audit.record(user, "store.kefu_submit_failed", "temu_kefu_action", action_id, exc.message, request)
        raise _store_http_error(exc) from None
    audit.record(
        user,
        "store.kefu_submit",
        "temu_kefu_action",
        action_id,
        f"{action['tool_name']} {action['data_type_name']} {action['data_id']}",
        request,
    )
    return action


@app.get("/api/store/kefu/actions")
def store_kefu_action_list(
    limit: int = Query(30, ge=1, le=100),
    stage: Optional[str] = Query(None, max_length=20),
    user: dict = Depends(store_user),
):
    return {"items": store_kefu_actions.list_actions(user, limit=limit, stage=stage)}


@app.get("/api/store/kefu/actions/{action_id}")
def store_kefu_action_get(action_id: str, user: dict = Depends(store_user)):
    try:
        return store_kefu_actions.get_action(user, action_id)
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.post("/api/store/kefu/actions/{action_id}/reply")
def store_kefu_action_reply(action_id: str, user: dict = Depends(store_user)):
    """拉机器人回执（前端在提交后每 3 秒轮询，最多 5 次）。"""
    try:
        return store_kefu_actions.fetch_reply(user, action_id)
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.get("/api/admin/kefu-actions")
def admin_kefu_actions(
    limit: int = Query(100, ge=1, le=200),
    stage: Optional[str] = Query(None, max_length=20),
    keyword: str = Query("", max_length=80),
    start: Optional[str] = Query(None, max_length=32),
    end: Optional[str] = Query(None, max_length=32),
    _admin: dict = Depends(admin_user),
):
    """跨用户的客服写操作审计。支持关键字（账号/工具/商品）与时间范围。"""
    def _parse(v):
        if not v:
            return None
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None

    return {
        "items": store_kefu_actions.list_all_actions(
            limit=limit,
            stage=stage,
            keyword=keyword,
            start=_parse(start),
            end=_parse(end),
        )
    }


@app.delete("/api/admin/kefu-actions")
def admin_kefu_actions_delete(payload: dict, _admin: dict = Depends(admin_user)):
    """批量删除客服操作审计记录。"""
    return {"deleted": delete_kefu_actions(payload.get("ids") or [])}


@app.get("/api/admin/store-logs")
def admin_store_logs(
    limit: int = Query(100, ge=1, le=500),
    action: Optional[str] = Query(None, max_length=32),
    user_id: Optional[int] = Query(None),
    keyword: str = Query("", max_length=80),
    start: Optional[str] = Query(None, max_length=32),
    end: Optional[str] = Query(None, max_length=32),
    _admin: dict = Depends(admin_user),
):
    """店铺登录/退出/失效事件（含 54001 冷却记录）。支持关键字与时间范围筛选。"""
    def _parse(v):
        if not v:
            return None
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None

    return {
        "items": list_temu_login_logs(
            limit=limit,
            user_id=user_id,
            action=action,
            keyword=keyword,
            start=_parse(start),
            end=_parse(end),
        )
    }


@app.delete("/api/admin/store-logs")
def admin_store_logs_delete(payload: dict, _admin: dict = Depends(admin_user)):
    """批量删除登录日志。"""
    ids = payload.get("ids") or []
    return {"deleted": delete_temu_login_logs(ids)}


@app.get("/api/regions")
def public_regions(_user: dict = Depends(current_user)):
    return {"items": list_regions()}


@app.get("/api/regions/{code}/settings")
def public_region_settings(code: str, category_code: Optional[str] = Query(None, max_length=16), _user: dict = Depends(current_user)):
    try:
        return get_region_profile(code, category_code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/settings")
def get_public_settings(
    region_code: Optional[str] = Query(None, max_length=16),
    category_code: Optional[str] = Query(None, max_length=16),
    _user: dict = Depends(current_user),
):
    try:
        return get_region_profile(region_code, category_code)["settings"]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/inventory/categories")
def inventory_categories(_user: dict = Depends(current_user)):
    from app.services.inventory import inventory_categories as list_inventory_categories
    return {"items": list_inventory_categories()}

@app.get("/api/admin/inventory/categories")
def admin_inventory_categories(_admin: dict = Depends(admin_user)):
    return {"items": list_inventory_category_defs(True)}


@app.post("/api/admin/inventory/categories", status_code=201)
def admin_create_inventory_category(payload: InventoryCategoryCreateRequest, request: Request, _admin: dict = Depends(admin_user)):
    key = payload.key.strip().upper()
    if not key.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="库存类目代码只能包含字母、数字、下划线或短横线")
    try:
        result = create_inventory_category_def(key, payload.label.strip(), payload.code_pattern, payload.price_max)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record(_admin, "inventory_category.create", "inventory_category", key, f"新增库存类目：{payload.label}", request)
    return result


@app.put("/api/admin/inventory/categories/{key}")
def admin_update_inventory_category(key: str, payload: InventoryCategoryUpdateRequest, request: Request, _admin: dict = Depends(admin_user)):
    try:
        result = update_inventory_category_def(key.strip().upper(), payload.label.strip(), payload.code_pattern, payload.price_max, payload.enabled, payload.sort_order)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record(_admin, "inventory_category.update", "inventory_category", key, f"更新库存类目：{payload.label}", request)
    return result


@app.delete("/api/admin/inventory/categories/{key}")
def admin_delete_inventory_category(key: str, request: Request, _admin: dict = Depends(admin_user)):
    try:
        delete_inventory_category_def(key.strip().upper())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record(_admin, "inventory_category.delete", "inventory_category", key, "删除库存类目", request)
    return {"message": "库存类目已删除", "key": key.strip().upper()}


@app.get("/api/inventory")
def get_inventory_status(inventory_category: Optional[str] = Query(None, max_length=16), _user: dict = Depends(current_user)):
    return inventory_status(inventory_category or "A")


@app.get("/api/inventory/items")
def list_inventory_items(
    query: str = Query("", max_length=80),
    set_type: str = Query("", max_length=64),
    source_sheet: str = Query("", max_length=255),
    price_min: Optional[float] = Query(None, ge=0),
    price_max: Optional[float] = Query(None, ge=0),
    row_min: Optional[int] = Query(None, ge=1),
    row_max: Optional[int] = Query(None, ge=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=10, le=200),
    inventory_category: Optional[str] = Query(None, max_length=16),
    user: dict = Depends(current_user),
):
    catalog = load_price_catalog(category=inventory_category or "A")
    items = filter_catalog_items(
        catalog,
        query=query,
        set_type=set_type,
        source_sheet=source_sheet,
        price_min=price_min,
        price_max=price_max,
        row_min=row_min,
        row_max=row_max,
    )
    start = (page - 1) * page_size
    return {"total": len(items), "items": items[start:start + page_size]}


@app.post("/api/inventory/preview")
async def stage_inventory_preview(request: Request, file: UploadFile = File(...), inventory_category: Optional[str] = Form(None), _admin: dict = Depends(admin_user)):
    from app.services.inventory import resolve_profile
    category = resolve_inventory_category(inventory_category)
    candidate = INVENTORY_DIR / f"pending_inventory_{category}.candidate.xlsx"
    await save_upload(file, candidate)
    try:
        state = await run_in_threadpool(stage_pending_upload, candidate, category)
    except Exception as exc:
        candidate.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"库存表无法读取：{exc}") from exc
    audit.record(_admin, "inventory.preview", "inventory", "库存统计表", f"上传[{category}类目]库存表待确认：{file.filename}，共 {state['sku_count']} 个 SKU", request)
    return {"message": "库存表已解析，请确认变更后应用", **state}


@app.get("/api/inventory/pending")
def get_pending_inventory(_admin: dict = Depends(admin_user)):
    all_states = all_pending_uploads()
    pending = next((state for state in all_states.values() if state is not None), None)
    return {"pending": pending, "all": all_states}


@app.post("/api/inventory/pending/apply")
def apply_pending_inventory(payload: InventoryApplyRequest, request: Request, _admin: dict = Depends(admin_user)):
    from app.services.inventory import resolve_profile
    category = resolve_inventory_category(payload.inventory_category)
    keep_skus = sorted({sku.strip().upper() for sku in payload.keep_skus if sku.strip()})
    skip_skus = sorted({sku.strip().upper() for sku in payload.skip_skus if sku.strip()})
    try:
        result = apply_pending_upload(keep_skus, skip_skus, category)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="没有待确认的库存上传") from exc
    merged = result["merged"]
    audit.record(
        _admin, "inventory.apply", "inventory", "库存统计表",
        f"应用[{category}类目]库存表变更：共 {len(merged)} 个 SKU，保留旧值 {len(keep_skus)} 项，跳过新增 {len(skip_skus)} 项",
        request,
    )
    return {
        "message": "库存表变更已应用",
        "sku_count": len(merged),
        "kept": len(keep_skus),
        "skipped": len(skip_skus),
        **inventory_status(category),
    }


@app.post("/api/inventory/pending/discard")
def discard_pending_inventory(request: Request, payload: Optional[InventoryApplyRequest] = None, _admin: dict = Depends(admin_user)):
    category = None
    if payload is not None and payload.inventory_category:
        from app.services.inventory import resolve_profile
        category = resolve_inventory_category(payload.inventory_category)
    discarded = discard_pending_upload(category)
    if discarded:
        audit.record(_admin, "inventory.discard", "inventory", "库存统计表", f"取消[{category or 'A'}类目]待确认的库存上传", request)
    return {"message": "已取消上传" if discarded else "当前没有待确认的上传", "discarded": discarded}


@app.post("/api/inventory")
async def upload_inventory(request: Request, file: UploadFile = File(...), inventory_category: Optional[str] = Form(None), _admin: dict = Depends(admin_user)):
    from app.services.inventory import inventory_path_for, resolve_profile
    category = resolve_inventory_category(inventory_category)
    destination = inventory_path_for(category)
    candidate = destination.with_name(f"库存统计表_{category}.candidate.xlsx")
    await save_upload(file, candidate)
    try:
        workbook = await run_in_threadpool(
            load_workbook, candidate, read_only=True, data_only=True, keep_links=False
        )
        workbook.close()
    except Exception as exc:
        candidate.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"库存表无法读取：{exc}") from exc
    os.replace(candidate, destination)
    invalidate_cache(category)
    audit.record(_admin, "inventory.upload", "inventory", "库存统计表", f"上传[{category}类目]库存表：{file.filename}", request)
    return {"message": "库存表已更新，缓存将在下次查询时自动重建", **inventory_status(category)}


def parse_activity_rules(value: Optional[str], allowed_pieces=None) -> Optional[dict]:
    if value is None:
        return None
    if len(value) > 20_000:
        raise HTTPException(status_code=400, detail="SKC识别规则内容过大")
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="SKC识别规则不是有效的JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="SKC识别规则必须是JSON对象")
    try:
        return normalize_parse_config(payload, allowed_pieces=allowed_pieces)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def parse_activity_id_profit_rules(value: Optional[str]) -> Optional[list[dict]]:
    if value is None:
        return None
    if len(value) > 200_000:
        raise HTTPException(status_code=400, detail="ID利润条件内容过大")
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="ID利润条件不是有效的JSON") from exc
    try:
        return normalize_id_profit_rules(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def parse_activity_drop_skc_list(value: Optional[str]) -> list[str]:
    """解析前端勾选的「要删除的无法识别 SKC」列表；未提供时返回空列表（不删行）。"""
    if value is None:
        return []
    if len(value) > 100_000:
        raise HTTPException(status_code=400, detail="要删除的SKC列表内容过大")
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="要删除的SKC列表不是有效的JSON") from exc
    try:
        return normalize_drop_skc_list(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/activities/preview")
async def preview_bulk_activity(
    file: UploadFile = File(...),
    skc_rules: Optional[str] = Form(None),
    id_profit_rules: Optional[str] = Form(None),
    region_code: Optional[str] = Form(None),
    category_code: Optional[str] = Form(None),
    page: int = Form(1, ge=1),
    page_size: int = Form(MAX_PREVIEW_ROWS, ge=1, le=MAX_PREVIEW_PAGE_SIZE),
    result_filter: Optional[str] = Form(None),
    _user: dict = Depends(current_user),
):
    validate_excel(file)
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="上传文件超过服务器限制")
    if result_filter is not None and result_filter not in PREVIEW_RESULT_FILTERS:
        raise HTTPException(status_code=400, detail="result_filter 只能是 单品 / 套装 / 无法识别")
    try:
        category = await run_in_threadpool(get_category, category_code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    parse_config = parse_activity_rules(skc_rules, category_allowed_pieces(category))
    parsed_id_profit_rules = parse_activity_id_profit_rules(id_profit_rules)
    try:
        snapshot = await run_in_threadpool(region_snapshot, region_code, category_code)
        return await run_in_threadpool(
            preview_activity_workbook,
            content,
            parse_config,
            snapshot["settings"],
            parsed_id_profit_rules,
            page,
            page_size,
            result_filter or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/activities/bulk", status_code=202)
async def process_bulk_activity(
    request: Request,
    file: UploadFile = File(...),
    uplift_limit: Optional[float] = Form(None, ge=0, le=1000),
    skc_rules: Optional[str] = Form(None),
    id_profit_rules: Optional[str] = Form(None),
    region_code: Optional[str] = Form(None),
    category_code: Optional[str] = Form(None),
    drop_unrecognized_skcs: Optional[str] = Form(None),
    # 未显式传值时默认删除全部无法识别的 SKC 所在行；显式传 false 才会保留
    drop_all_unrecognized: Optional[bool] = Form(None),
    user: dict = Depends(current_user),
):
    validate_excel(file)
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="上传文件超过服务器限制")

    upload_name = file.filename or "报名活动.xlsx"
    try:
        category = await run_in_threadpool(get_category, category_code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    parse_config = parse_activity_rules(skc_rules, category_allowed_pieces(category))
    parsed_id_profit_rules = parse_activity_id_profit_rules(id_profit_rules)
    drop_skcs = parse_activity_drop_skc_list(drop_unrecognized_skcs)
    if drop_all_unrecognized is not None:
        drop_everything = bool(drop_all_unrecognized)
    else:
        # 两个参数都不传 → 默认删除全部；显式传了 SKC 列表（即使是空数组）→ 按列表执行
        drop_everything = drop_unrecognized_skcs is None
    try:
        snapshot = await run_in_threadpool(region_snapshot, region_code, category_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if activity_task_manager.queue_full():
        raise HTTPException(status_code=429, detail="后台任务队列已满，请稍后再试")
    try:
        job = await run_in_threadpool(
            activity_task_manager.create,
            upload_name,
            user["id"],
            content,
            snapshot,
            uplift_limit,
            parse_config,
            parsed_id_profit_rules,
            drop_skcs,
            drop_everything,
        )
    except QueueFullError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    if drop_everything:
        drop_note = "，删除全部无法识别的SKC所在行"
    elif drop_skcs:
        drop_note = f"，删除{len(drop_skcs)}个无法识别的SKC所在行"
    else:
        drop_note = ""
    audit.record(user, "activity.create", "activity_job", job["id"], f"提交批量报名活动：{upload_name}（区域 {snapshot['region']['code']} / 品类 {snapshot['category']['code']}）{drop_note}", request)
    return {
        **job,
        "download_url": f"/api/activities/{job['id']}/download",
    }


@app.get("/api/activities")
def list_activity_tasks(limit: int = Query(50, ge=1, le=100), user: dict = Depends(current_user)):
    return {"items": activity_task_manager.list(user["id"], limit)}


@app.get("/api/activities/{job_id}")
def get_activity_task(job_id: str, user: dict = Depends(current_user)):
    job = activity_task_manager.get(job_id, user["id"])
    if not job:
        raise HTTPException(status_code=404, detail="活动任务不存在")
    return job


@app.delete("/api/activities/{job_id}")
def delete_activity_task(job_id: str, request: Request, user: dict = Depends(current_user)):
    result = activity_task_manager.delete(job_id, user["id"])
    if result == "active":
        raise HTTPException(status_code=409, detail="处理中任务暂不能删除，请等待任务完成")
    if result == "not_found":
        raise HTTPException(status_code=404, detail="活动任务不存在")
    audit.record(user, "activity.delete", "activity_job", job_id, "删除自己的报名活动任务", request)
    return {"message": "活动任务记录已删除", "id": job_id}


@app.get("/api/activities/{job_id}/download")
def download_activity(job_id: str, user: dict = Depends(current_user)):
    output_path = activity_task_manager.result_path(job_id, user["id"])
    if not output_path:
        raise HTTPException(status_code=404, detail="处理结果不存在或已过期")
    return FileResponse(
        output_path,
        filename=output_path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.post("/api/inventory/rebuild")
async def rebuild_inventory(request: Request, inventory_category: Optional[str] = Form(None), _admin: dict = Depends(admin_user)):
    category = resolve_inventory_category(inventory_category)
    invalidate_cache(category)
    catalog = await run_in_threadpool(load_price_catalog, category=category)
    audit.record(_admin, "inventory.rebuild", "inventory", "price_cache", f"重建[{category}类目]库存缓存，共 {len(catalog)} 个 SKU", request)
    return {"message": "库存缓存已重建", "sku_count": len(catalog), **inventory_status(category)}


@app.get("/api/admin/inventory")
def admin_inventory_status(inventory_category: Optional[str] = Query(None, max_length=16), _admin: dict = Depends(admin_user)):
    return inventory_status(inventory_category or "A")


@app.get("/api/admin/inventory/items")
def admin_inventory_items(
    query: str = Query("", max_length=80),
    set_type: str = Query("", max_length=64),
    source_sheet: str = Query("", max_length=255),
    price_min: Optional[float] = Query(None, ge=0),
    price_max: Optional[float] = Query(None, ge=0),
    row_min: Optional[int] = Query(None, ge=1),
    row_max: Optional[int] = Query(None, ge=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=10, le=200),
    inventory_category: Optional[str] = Query(None, max_length=16),
    _admin: dict = Depends(admin_user),
):
    catalog = load_price_catalog(category=inventory_category or "A")
    items = filter_catalog_items(
        catalog,
        query=query,
        set_type=set_type,
        source_sheet=source_sheet,
        price_min=price_min,
        price_max=price_max,
        row_min=row_min,
        row_max=row_max,
    )
    start = (page - 1) * page_size
    return {"total": len(items), "items": items[start:start + page_size]}


@app.post("/api/admin/inventory/items", status_code=201)
def admin_create_inventory_item(payload: InventoryItemCreateRequest, request: Request, _admin: dict = Depends(admin_user)):
    normalized = payload.sku.strip().upper()
    if not normalized:
        raise HTTPException(status_code=400, detail="SKU 不能为空")
    try:
        item = create_inventory_item(normalized, payload.price, payload.set_type.strip() or "单品", payload.inventory_category or "A", payload.manual_combo)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit.record(_admin, "inventory.item_create", "inventory_item", normalized, f"新增库存明细：{normalized}{'（手动组合）' if payload.manual_combo else ''}", request)
    return {"message": "库存明细已添加", "item": item, **inventory_status(payload.inventory_category or "A")}


@app.put("/api/admin/inventory/items/{sku}")
def admin_update_inventory_item(sku: str, payload: InventoryItemUpdateRequest, request: Request, _admin: dict = Depends(admin_user)):
    old_sku = sku.strip().upper()
    normalized = payload.sku.strip().upper()
    if not old_sku or not normalized:
        raise HTTPException(status_code=400, detail="SKU 不能为空")
    try:
        item = update_inventory_item(old_sku, normalized, payload.price, payload.set_type.strip() or "单品", payload.inventory_category or "A", payload.manual_combo)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not item:
        raise HTTPException(status_code=404, detail="库存 SKU 不存在")
    audit.record(_admin, "inventory.item_update", "inventory_item", normalized, f"更新库存明细：{old_sku} → {normalized}{'（手动组合）' if payload.manual_combo else ''}", request)
    return {"message": "库存明细已更新", "item": item, **inventory_status(payload.inventory_category or "A")}


@app.delete("/api/admin/inventory/items/{sku}")
def admin_delete_inventory_item(sku: str, request: Request, inventory_category: Optional[str] = Query(None, max_length=16), _admin: dict = Depends(admin_user)):
    normalized = sku.strip().upper()
    if not normalized or not delete_inventory_item(normalized, inventory_category or "A"):
        raise HTTPException(status_code=404, detail="库存 SKU 不存在")
    audit.record(_admin, "inventory.item_delete", "inventory_item", normalized, f"删除库存明细：{normalized}", request)
    return {"message": "库存明细已删除", "sku": normalized, **inventory_status(inventory_category or "A")}


@app.post("/api/skus/query")
async def query_skus(request: SkuQueryRequest, _user: dict = Depends(current_user)):
    normalized = list(dict.fromkeys(sku.strip().upper() for sku in request.skus if sku.strip()))
    if not normalized:
        raise HTTPException(status_code=400, detail="请输入至少一个 SKU")
    catalog = await run_in_threadpool(load_price_catalog, category=request.inventory_category or "A")
    results = []
    for sku in normalized:
        item = catalog.get(sku)
        results.append({"sku": sku, "found": item is not None, **(item or {})})
    return {"total": len(normalized), "found": sum(item["found"] for item in results), "items": results}


@app.get("/api/half-headcost")
def list_half_headcost(
    query: str = Query("", max_length=80),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=10, le=200),
    category_code: Optional[str] = Query(None, max_length=16),
    inventory_category: Optional[str] = Query(None, max_length=16),
    _user: dict = Depends(current_user),
):
    try:
        category = get_category(category_code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    inventory_key = resolve_inventory_category(inventory_category)
    entries = load_entries(category["id"], inventory_key)
    keyword = query.strip().upper()
    items = [
        {"sku": sku, "set_type": set_type}
        for sku, set_type in sorted(entries.items())
        if not keyword or keyword in sku.upper()
    ]
    start = (page - 1) * page_size
    return {"total": len(items), "items": items[start:start + page_size], "category": {"code": category["code"], "name": category["name"]}, "inventory_category": inventory_key}


@app.post("/api/half-headcost/import")
async def import_half_headcost(
    request: Request,
    file: UploadFile = File(...),
    category_code: Optional[str] = Form(None),
    inventory_category: Optional[str] = Form(None),
    _admin: dict = Depends(admin_user),
):
    validate_excel(file)
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="上传文件超过服务器限制")
    try:
        category = get_category(category_code, include_disabled=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    inventory_key = resolve_inventory_category(inventory_category)
    try:
        result = await run_in_threadpool(merge_upload, content, category["id"], inventory_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record(_admin, "half_headcost.import", "half_headcost", category["code"], f"导入头程减半名单（品类 {category['code']}，库存类目 {inventory_key}）：{file.filename}（新增 {result['added']}）", request)
    return {"message": "头程减半名单已合并", "inventory_category": inventory_key, **result}


@app.delete("/api/half-headcost/{sku}")
def remove_half_headcost(sku: str, request: Request, category_code: Optional[str] = Query(None, max_length=16), inventory_category: Optional[str] = Query(None, max_length=16), _admin: dict = Depends(admin_user)):
    normalized = sku.strip().upper()
    try:
        category = get_category(category_code, include_disabled=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    inventory_key = resolve_inventory_category(inventory_category)
    if not delete_entry(normalized, category["id"], inventory_key):
        raise HTTPException(status_code=404, detail="SKU 不在头程减半名单中")
    audit.record(_admin, "half_headcost.delete", "half_headcost", normalized, f"从头程减半名单删除（品类 {category['code']}，库存类目 {inventory_key}）：{normalized}", request)
    return {"message": "已删除", "sku": normalized, "inventory_category": inventory_key}



@app.post("/api/admin/half-headcost", status_code=201)
def admin_create_half_headcost(payload: HalfHeadcostCreateRequest, request: Request, _admin: dict = Depends(admin_user)):
    normalized = payload.sku.strip().upper()
    if not normalized:
        raise HTTPException(status_code=400, detail="SKU 不能为空")
    try:
        category = get_category(payload.category_code, include_disabled=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    inventory_key = resolve_inventory_category(payload.inventory_category)
    existed = entry_exists(normalized, category["id"], inventory_key)
    try:
        item = save_entry(normalized, payload.set_type, category["id"], inventory_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record(
        _admin,
        "half_headcost.create",
        "half_headcost",
        normalized,
        f"{'覆盖' if existed else '新增'}头程减半名单（品类 {category['code']}，库存类目 {inventory_key}）：{normalized}（{item['set_type']}）",
        request,
    )
    message = "该 SKU 已存在，已覆盖类型" if existed else "头程减半名单已添加"
    return {"message": message, "item": item, "category": {"code": category["code"], "name": category["name"]}, "inventory_category": inventory_key}


@app.put("/api/admin/half-headcost/{sku}")
def admin_update_half_headcost(sku: str, payload: HalfHeadcostUpdateRequest, request: Request, _admin: dict = Depends(admin_user)):
    normalized = sku.strip().upper()
    if not normalized:
        raise HTTPException(status_code=400, detail="SKU 不能为空")
    try:
        category = get_category(payload.category_code, include_disabled=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    inventory_key = resolve_inventory_category(payload.inventory_category)
    if not entry_exists(normalized, category["id"], inventory_key):
        raise HTTPException(status_code=404, detail="SKU 不在头程减半名单中")
    try:
        item = save_entry(normalized, payload.set_type, category["id"], inventory_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record(_admin, "half_headcost.update", "half_headcost", normalized, f"更新头程减半名单（品类 {category['code']}，库存类目 {inventory_key}）：{normalized} → {item['set_type']}", request)
    return {"message": "头程减半名单已更新", "item": item, "category": {"code": category["code"], "name": category["name"]}, "inventory_category": inventory_key}


def _weight_item(sku: str, weight_g: float) -> dict:
    return {"sku": sku, "weight_g": round(float(weight_g), 2), "headcost": headcost_for_weight(weight_g)}


@app.get("/api/weight-headcost")
def list_weight_headcost(
    query: str = Query("", max_length=80),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=10, le=200),
    category_code: Optional[str] = Query(None, max_length=16),
    inventory_category: Optional[str] = Query(None, max_length=16),
    _user: dict = Depends(current_user),
):
    try:
        category = get_category(category_code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    inventory_key = resolve_inventory_category(inventory_category)
    entries = load_weight_entries(category["id"], inventory_key)
    keyword = query.strip().upper()
    items = [
        _weight_item(sku, weight)
        for sku, weight in sorted(entries.items())
        if not keyword or keyword in sku.upper()
    ]
    start = (page - 1) * page_size
    return {"total": len(items), "items": items[start:start + page_size], "category": {"code": category["code"], "name": category["name"]}, "inventory_category": inventory_key}


@app.post("/api/weight-headcost/import")
async def import_weight_headcost(
    request: Request,
    file: UploadFile = File(...),
    category_code: Optional[str] = Form(None),
    inventory_category: Optional[str] = Form(None),
    _admin: dict = Depends(admin_user),
):
    validate_excel(file)
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="上传文件超过服务器限制")
    try:
        category = get_category(category_code, include_disabled=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    inventory_key = resolve_inventory_category(inventory_category)
    try:
        result = await run_in_threadpool(merge_weight_upload, content, category["id"], inventory_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record(_admin, "weight_headcost.import", "weight_headcost", category["code"], f"导入克重计算头程名单（品类 {category['code']}，库存类目 {inventory_key}）：{file.filename}（新增 {result['added']}，更新 {result['updated']}）", request)
    return {"message": "克重计算头程名单已合并", "inventory_category": inventory_key, **result}


@app.delete("/api/weight-headcost/{sku}")
def remove_weight_headcost(sku: str, request: Request, category_code: Optional[str] = Query(None, max_length=16), inventory_category: Optional[str] = Query(None, max_length=16), _admin: dict = Depends(admin_user)):
    normalized = sku.strip().upper()
    try:
        category = get_category(category_code, include_disabled=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    inventory_key = resolve_inventory_category(inventory_category)
    if not delete_weight_entry(normalized, category["id"], inventory_key):
        raise HTTPException(status_code=404, detail="编号不在克重计算头程名单中")
    audit.record(_admin, "weight_headcost.delete", "weight_headcost", normalized, f"从克重计算头程名单删除（品类 {category['code']}，库存类目 {inventory_key}）：{normalized}", request)
    return {"message": "已删除", "sku": normalized, "inventory_category": inventory_key}


@app.post("/api/admin/weight-headcost", status_code=201)
def admin_create_weight_headcost(payload: WeightHeadcostCreateRequest, request: Request, _admin: dict = Depends(admin_user)):
    normalized = payload.sku.strip().upper()
    if not normalized:
        raise HTTPException(status_code=400, detail="编号不能为空")
    try:
        category = get_category(payload.category_code, include_disabled=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    inventory_key = resolve_inventory_category(payload.inventory_category)
    existed = weight_entry_exists(normalized, category["id"], inventory_key)
    try:
        item = save_weight_entry(normalized, payload.weight_g, category["id"], inventory_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record(
        _admin,
        "weight_headcost.create",
        "weight_headcost",
        normalized,
        f"{'覆盖' if existed else '新增'}克重计算头程名单（品类 {category['code']}，库存类目 {inventory_key}）：{normalized}（{item['weight_g']}g）",
        request,
    )
    return {"message": "该编号已存在，已覆盖克重" if existed else "克重计算头程名单已添加", "item": item, "category": {"code": category["code"], "name": category["name"]}, "inventory_category": inventory_key}


@app.put("/api/admin/weight-headcost/{sku}")
def admin_update_weight_headcost(sku: str, payload: WeightHeadcostUpdateRequest, request: Request, _admin: dict = Depends(admin_user)):
    normalized = sku.strip().upper()
    if not normalized:
        raise HTTPException(status_code=400, detail="编号不能为空")
    try:
        category = get_category(payload.category_code, include_disabled=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    inventory_key = resolve_inventory_category(payload.inventory_category)
    if not weight_entry_exists(normalized, category["id"], inventory_key):
        raise HTTPException(status_code=404, detail="编号不在克重计算头程名单中")
    try:
        item = save_weight_entry(normalized, payload.weight_g, category["id"], inventory_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record(_admin, "weight_headcost.update", "weight_headcost", normalized, f"更新克重计算头程名单（品类 {category['code']}，库存类目 {inventory_key}）：{normalized} → {item['weight_g']}g", request)
    return {"message": "克重计算头程名单已更新", "item": item, "category": {"code": category["code"], "name": category["name"]}, "inventory_category": inventory_key}


@app.post("/api/tasks", status_code=202)
async def create_task(
    request: Request,
    sales: UploadFile = File(...),
    delivery: UploadFile = File(...),
    half_headcost: Optional[UploadFile] = File(None),
    region_code: Optional[str] = Form(None),
    category_code: Optional[str] = Form(None),
    user: dict = Depends(current_user),
):
    if task_manager.queue_full():
        raise HTTPException(status_code=429, detail="后台任务队列已满，请稍后再试")
    validate_excel(sales)
    validate_excel(delivery)
    if half_headcost:
        validate_excel(half_headcost)
    try:
        snapshot = await run_in_threadpool(region_snapshot, region_code, category_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    bound_inventory = (snapshot.get("category", {}) or {}).get("inventory_category") or "A"
    if not inventory_path_for(bound_inventory).exists():
        raise HTTPException(status_code=409, detail=f"服务器尚未配置{bound_inventory}类目库存统计表")
    task = task_manager.create(
        sales.filename or "销售订单.xlsx",
        delivery.filename or "派送订单.xlsx",
        half_headcost.filename if half_headcost else None,
        user["id"],
        snapshot,
    )
    try:
        await save_upload(sales, task_manager.file_path(task["id"], "sales"))
        await save_upload(delivery, task_manager.file_path(task["id"], "delivery"))
        if half_headcost:
            await save_upload(half_headcost, task_manager.file_path(task["id"], "half_headcost"))
    except Exception:
        raise
    task_manager.queue(task["id"])
    audit.record(user, "task.create", "task", task["id"], f"提交订单计算（区域 {snapshot['region']['code']} / 品类 {snapshot['category']['code']}）", request)
    return task_manager.get(task["id"], user["id"])


@app.get("/api/tasks")
def list_tasks(limit: int = Query(30, ge=1, le=100), user: dict = Depends(current_user)):
    return {"items": task_manager.list(limit, user["id"])}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str, user: dict = Depends(current_user)):
    task = task_manager.get(task_id, user["id"])
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str, request: Request, user: dict = Depends(current_user)):
    result = task_manager.delete(task_id, user["id"])
    if result == "active":
        raise HTTPException(status_code=409, detail="处理中任务暂不能删除，请等待任务完成")
    if result == "not_found":
        raise HTTPException(status_code=404, detail="任务不存在")
    audit.record(user, "task.delete", "task", task_id, "删除自己的订单计算任务", request)
    return {"message": "任务记录已删除", "id": task_id}


@app.get("/api/tasks/{task_id}/download")
def download_task(task_id: str, user: dict = Depends(current_user)):
    result = task_manager.result_path(task_id, user["id"])
    if not result:
        raise HTTPException(status_code=404, detail="结果文件尚未生成")
    return FileResponse(
        result,
        filename="销售订单汇总表.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/api/admin/tasks")
def admin_tasks(limit: int = Query(100, ge=1, le=500), _admin: dict = Depends(admin_user)):
    users = {user["id"]: user for user in list_users()}
    items = []
    for task in task_manager.list(limit):
        owner = users.get(task.get("owner_id"))
        items.append({
            **task,
            "owner_name": (owner.get("display_name") or owner.get("username")) if owner else "历史用户",
            "owner_username": owner.get("username") if owner else "-",
        })
    return {"items": items}


@app.get("/api/admin/activity-tasks")
def admin_activity_tasks(limit: int = Query(100, ge=1, le=500), _admin: dict = Depends(admin_user)):
    users = {user["id"]: user for user in list_users()}
    items = []
    for task in activity_task_manager.list_admin(limit):
        owner = users.get(task.get("owner_id"))
        items.append({
            **task,
            "owner_name": (owner.get("display_name") or owner.get("username")) if owner else "历史用户",
            "owner_username": owner.get("username") if owner else "-",
        })
    return {"items": items}


@app.delete("/api/admin/tasks/{task_id}")
def admin_delete_task(task_id: str, request: Request, _admin: dict = Depends(admin_user)):
    result = task_manager.delete(task_id, None)
    if result == "active":
        raise HTTPException(status_code=409, detail="处理中任务暂不能删除，请等待任务完成")
    if result == "not_found":
        raise HTTPException(status_code=404, detail="任务不存在")
    audit.record(_admin, "task.admin_delete", "task", task_id, "管理员删除订单计算任务", request)
    return {"message": "任务记录已删除", "id": task_id}


@app.get("/api/admin/tasks/{task_id}/download")
def admin_download_task(task_id: str, _admin: dict = Depends(admin_user)):
    result = task_manager.result_path(task_id, None)
    if not result:
        raise HTTPException(status_code=404, detail="结果文件尚未生成")
    return FileResponse(
        result,
        filename="销售订单汇总表.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.delete("/api/admin/activity-tasks/{job_id}")
def admin_delete_activity_task(job_id: str, request: Request, _admin: dict = Depends(admin_user)):
    result = activity_task_manager.delete(job_id, None)
    if result == "active":
        raise HTTPException(status_code=409, detail="处理中任务暂不能删除，请等待任务完成")
    if result == "not_found":
        raise HTTPException(status_code=404, detail="活动任务不存在")
    audit.record(_admin, "activity.admin_delete", "activity_job", job_id, "管理员删除报名活动任务", request)
    return {"message": "活动任务记录已删除", "id": job_id}


@app.get("/api/admin/activity-tasks/{job_id}/download")
def admin_download_activity(job_id: str, _admin: dict = Depends(admin_user)):
    output_path = activity_task_manager.result_path(job_id, None)
    if not output_path:
        raise HTTPException(status_code=404, detail="处理结果不存在或已过期")
    return FileResponse(
        output_path,
        filename=output_path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

@app.get("/api/admin/store-users")
def admin_store_users(_admin: dict = Depends(admin_user)):
    """后台店铺管理：开通店铺的用户 + 建品统计 + 模板数 + 会话状态。"""
    return product_templates.admin_user_overview()


@app.get("/api/admin/store-users/{user_id}/templates")
def admin_store_user_templates(user_id: int, _admin: dict = Depends(admin_user)):
    """某用户保存的表单模板（后台只读查看 + 删除）。"""
    return product_templates.admin_user_templates(user_id)


@app.delete("/api/admin/store-users/{user_id}/templates/{template_id}")
def admin_store_user_template_delete(user_id: int, template_id: int, _admin: dict = Depends(admin_user)):
    """管理员删除某用户的模板。"""
    try:
        return product_templates.delete_template(user_id, template_id)
    except StoreError as exc:
        raise _store_http_error(exc) from None


@app.get("/api/admin/store-sessions")
def admin_store_sessions(_admin: dict = Depends(admin_user)):
    """店铺会话总览（账号一律脱敏；用于排障与强制下线）。"""
    return {"items": store_session.list_all()}


@app.delete("/api/admin/store-sessions/{user_id}")
def admin_revoke_store_session(user_id: int, request: Request, _admin: dict = Depends(admin_user)):
    target = get_user(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    revoked = store_session.revoke(user_id, reason="admin_revoked", ip=_client_ip(request))
    audit.record(_admin, "store.revoke", "user", target["username"], "管理员强制退出店铺会话", request)
    return {"revoked": bool(revoked)}


@app.get("/api/permissions")
def permission_catalog(_user: dict = Depends(current_user)):
    """模块清单（前端渲染菜单/权限勾选用）。"""
    return permissions.catalog()


@app.get("/api/admin/users")
def admin_users(_admin: dict = Depends(admin_user)):
    return {"items": list_users()}


@app.get("/api/admin/users/{user_id}/store-accounts")
def admin_view_store_accounts(
    user_id: int,
    request: Request,
    with_password: bool = Query(False),
    admin: dict = Depends(admin_user),
):
    """管理员查看某个用户保存的所有店铺。

    `with_password=true` 时一并解密密码（明文，会写审计）；默认只给脱敏账号。
    """
    if not get_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    if with_password and not credential_cipher.enabled():
        raise HTTPException(status_code=409, detail="未配置 CREDENTIAL_KEY，无法解密店铺密码")
    items = store_accounts.list_for_user(user_id, with_password=with_password)
    binding = store_session.user_binding(user_id)
    active = binding.get("account_masked") if binding.get("bound") else ""
    for item in items:
        item["active"] = bool(active) and item["account_masked"] == active
    if with_password:
        audit.record(
            admin,
            "store.view_accounts",
            "temu_store_account",
            f"user#{user_id}",
            f"查看 {len(items)} 个店铺账号密码",
            request,
        )
    return {"items": items, "active_account_masked": active}


@app.get("/api/admin/users/{user_id}/store-binding")
def admin_view_store_binding(user_id: int, admin: dict = Depends(admin_user)):
    """管理员查看某个用户已绑定的店铺信息（账号脱敏，不含密码）。"""
    if not get_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    return store_session.user_binding(user_id)


@app.get("/api/admin/users/{user_id}/store-credential")
def admin_view_store_credential(user_id: int, request: Request, admin: dict = Depends(admin_user)):
    """管理员查看某个用户保存的店铺账号密码（明文，操作会写审计）。"""
    target = get_user(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not credential_cipher.enabled():
        raise HTTPException(status_code=409, detail="未配置 CREDENTIAL_KEY，无法解密已保存的店铺密码")
    record = get_temu_credential(user_id)
    if not record or not record.get("ciphertext"):
        raise HTTPException(status_code=404, detail="该用户尚未保存店铺账号密码")
    try:
        password = credential_cipher.decrypt(record["ciphertext"])
    except Exception:  # noqa: BLE001 - 密钥更换/密文损坏
        raise HTTPException(status_code=500, detail="密码解密失败（CREDENTIAL_KEY 可能已更换）") from None
    audit.record(
        admin,
        "store.view_credential",
        "temu_credential",
        record["account"],
        "管理员查看店铺账号密码",
        request,
    )
    return {
        "account": record["account"],
        "password": password,
        "updated_at": record["updated_at"],
    }


@app.post("/api/admin/users/{user_id}/approve")
@app.post("/api/admin/users/{user_id}/enable", include_in_schema=False)
def approve_user(user_id: int, request: Request, _admin: dict = Depends(admin_user)):
    target = get_user(user_id)
    if not target or target["role"] == "admin":
        raise HTTPException(status_code=404, detail="普通用户不存在")
    user = update_user_status(user_id, "approved")
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    audit.record(_admin, "user.enable", "user", user["username"], "启用用户账号", request)
    return public_user(user)


@app.post("/api/admin/users/{user_id}/disable")
@app.post("/api/admin/users/{user_id}/reject", include_in_schema=False)
def disable_user(user_id: int, request: Request, _admin: dict = Depends(admin_user)):
    target = get_user(user_id)
    if not target or target["role"] == "admin":
        raise HTTPException(status_code=404, detail="普通用户不存在")
    user = update_user_status(user_id, "rejected")
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    audit.record(_admin, "user.disable", "user", user["username"], "禁用用户账号", request)
    return public_user(user)


@app.patch("/api/admin/users/bulk")
def admin_bulk_update_users(payload: AdminUserBulkUpdateRequest, request: Request, _admin: dict = Depends(admin_user)):
    fields = payload.model_fields_set
    if not fields.intersection({"display_name", "note", "status", "store_access"}):
        raise HTTPException(status_code=400, detail="请至少选择一个要批量修改的字段")
    try:
        items = bulk_update_users(
            payload.user_ids,
            display_name=(payload.display_name or "").strip() if "display_name" in fields else None,
            note=(payload.note or "").strip() if "note" in fields else None,
            status=payload.status if "status" in fields else None,
            store_access=payload.store_access if "store_access" in fields else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # 收回模块权限时必须同时销毁店铺会话：否则会话仍占着该 Temu 账号，别处无法登录
    revoked = 0
    if "store_access" in fields and payload.store_access is False:
        for item in items:
            if store_session.revoke(item["id"], reason="access_revoked", ip=_client_ip(request)):
                revoked += 1
    audit.record(_admin, "user.bulk_update", "user", "bulk", f"批量更新 {len(items)} 个用户", request)
    return {"items": items, "updated": len(items), "store_sessions_revoked": revoked}


@app.post("/api/admin/users/bulk-delete")
def admin_bulk_delete_users(payload: AdminUserBulkDeleteRequest, request: Request, _admin: dict = Depends(admin_user)):
    try:
        items = bulk_delete_users(payload.user_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record(_admin, "user.bulk_delete", "user", "bulk", f"批量删除 {len(items)} 个用户", request)
    return {"deleted_ids": [item["id"] for item in items], "deleted": len(items)}


@app.patch("/api/admin/users/{user_id}")
def admin_update_user(user_id: int, payload: AdminUserUpdateRequest, request: Request, _admin: dict = Depends(admin_user)):
    target = get_user(user_id)
    if not target or target["role"] == "admin":
        raise HTTPException(status_code=404, detail="普通用户不存在")
    fields = payload.model_fields_set
    username = target["username"]
    if payload.username is not None:
        try:
            username = validate_username(payload.username)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        existing = get_user_by_username(username)
        if existing and existing["id"] != user_id:
            raise HTTPException(status_code=409, detail="用户名已存在")
    store_access = payload.store_access if "store_access" in fields else None
    # 模块权限：只传了的才改，未传保持原样
    modules = payload.modules if "modules" in fields else None
    store_modules = payload.store_modules if "store_modules" in fields else None
    user = update_user_credentials(
        user_id,
        username=username if payload.username is not None else None,
        password_hash=hash_password(payload.password) if payload.password else None,
        display_name=(payload.display_name or "").strip() if "display_name" in fields else None,
        note=(payload.note or "").strip() if "note" in fields else None,
        store_access=store_access,
        modules=modules,
        store_modules=store_modules,
    )
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    # 关闭店铺管理模块 → 立刻销毁该用户的店铺会话（释放被占用的 Temu 账号）
    revoked = False
    if store_access is False:
        revoked = store_session.revoke(user_id, reason="access_revoked", ip=_client_ip(request))
    changed = [name for name in ("username", "display_name", "note", "store_access") if name in fields]
    if payload.password:
        changed.append("password")
    audit.record(_admin, "user.update", "user", user["username"], f"修改用户账号（{', '.join(changed) or '无字段变化'}）", request)
    if store_access is not None:
        audit.record(
            _admin,
            "user.store_access",
            "user",
            user["username"],
            f"{'开通' if store_access else '关闭'}店铺管理模块"
            + ("（已强制退出店铺会话）" if revoked else ""),
            request,
        )
    return user


@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: int, request: Request, _admin: dict = Depends(admin_user)):
    target = get_user(user_id)
    if not target or target["role"] == "admin":
        raise HTTPException(status_code=404, detail="普通用户不存在")
    if not delete_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    audit.record(_admin, "user.delete", "user", target["username"], "删除用户", request)
    return {"message": "用户已删除", "id": user_id}


@app.get("/api/admin/settings")
def admin_get_settings(
    region_code: Optional[str] = Query(None, max_length=16),
    category_code: Optional[str] = Query(None, max_length=16),
    _admin: dict = Depends(admin_user),
):
    try:
        return get_region_profile(region_code, category_code, include_disabled=True)["settings"]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/admin/settings")
def admin_update_settings(
    payload: SettingsPayload,
    request: Request,
    region_code: Optional[str] = Query(None, max_length=16),
    category_code: Optional[str] = Query(None, max_length=16),
    admin: dict = Depends(admin_user),
):
    try:
        profile = get_region_profile(region_code, category_code, include_disabled=True)
        updated = update_region(profile["code"], {**profile, "settings": payload.model_dump()}, admin["id"], category_code=profile["category"]["code"])
        audit.record(admin, "settings.update", "region", f"{profile['code']}/{profile['category']['code']}", "更新成本参数", request)
        return updated["settings"]
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/admin/categories")
def admin_categories(_admin: dict = Depends(admin_user)):
    return {"items": list_categories(include_disabled=True), "template_types": TEMPLATE_TYPES}


@app.post("/api/admin/categories", status_code=201)
def admin_create_category(payload: CategoryCreateRequest, request: Request, _admin: dict = Depends(admin_user)):
    try:
        category = create_category(payload.model_dump(), _admin["id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record(_admin, "category.create", "category", category["code"], f"新增品类 {category['name']}（模版 {category['template_label']}）", request)
    return category


@app.put("/api/admin/categories/{code}")
def admin_update_category(code: str, payload: CategoryUpdateRequest, request: Request, _admin: dict = Depends(admin_user)):
    try:
        category = update_category(code, payload.model_dump(), _admin["id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record(_admin, "category.update", "category", category["code"], f"更新品类 {category['name']}", request)
    return category


@app.delete("/api/admin/categories/{code}")
def admin_delete_category(code: str, request: Request, _admin: dict = Depends(admin_user)):
    try:
        delete_category(code)
        audit.record(_admin, "category.delete", "category", code.strip().upper(), "删除品类及其配置", request)
        return {"message": "品类已删除", "code": code.strip().upper()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/admin/activity-settings/skc-rules")
def admin_get_activity_skc_rules(category_code: Optional[str] = Query(None, max_length=16), _admin: dict = Depends(admin_user)):
    try:
        category = get_category(category_code, include_disabled=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return category_skc_rules(category)


@app.put("/api/admin/activity-settings/skc-rules")
def admin_update_activity_skc_rules(
    payload: ActivitySkuRulesPayload,
    request: Request,
    category_code: Optional[str] = Query(None, max_length=16),
    _admin: dict = Depends(admin_user),
):
    try:
        category = get_category(category_code, include_disabled=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        rules = normalize_parse_config(payload.model_dump(), allowed_pieces=category_allowed_pieces(category))
        if rules is None:
            raise ValueError("默认SKC识别规则不能为空")
        saved = save_activity_skc_rules(rules, category["code"])
        audit.record(_admin, "activity_rules.update", "settings", f"skc_rules/{category['code']}", "更新默认SKC识别规则", request)
        return saved
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.get("/api/admin/regions")
def admin_regions(_admin: dict = Depends(admin_user)):
    return {"items": list_regions(include_disabled=True)}


@app.get("/api/admin/regions/{code}")
def admin_region(code: str, category_code: Optional[str] = Query(None, max_length=16), _admin: dict = Depends(admin_user)):
    try:
        return get_region_profile(code, category_code, include_disabled=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/admin/regions", status_code=201)
def admin_create_region(payload: RegionCreateRequest, request: Request, admin: dict = Depends(admin_user)):
    try:
        region = create_region(payload.model_dump(), admin["id"])
        audit.record(admin, "region.create", "region", region["code"], f"新增区域 {region['name']}", request)
        return region
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/admin/regions/{code}")
def admin_update_region(code: str, payload: RegionUpdateRequest, request: Request, category_code: Optional[str] = Query(None, max_length=16), admin: dict = Depends(admin_user)):
    try:
        region = update_region(code, payload.model_dump(), admin["id"], category_code=category_code)
        audit.record(admin, "region.update", "region", region["code"], "更新区域配置", request)
        return region
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/admin/regions/{code}")
def admin_delete_region(code: str, request: Request, _admin: dict = Depends(admin_user)):
    try:
        delete_region(code)
        audit.record(_admin, "region.delete", "region", code.strip().upper(), "删除区域", request)
        return {"message": "区域已删除", "code": code.strip().upper()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/admin/system-settings")
def admin_get_system_settings(_admin: dict = Depends(admin_user)):
    return {
        "settings": system.get(),
        "pools": {
            "orders": task_manager.pool_stats(),
            "activities": activity_task_manager.pool_stats(),
        },
        "cleanup": {
            "interval_seconds": CLEANUP_INTERVAL_SECONDS,
            "last_run_at": cleanup_scheduler.last_run_at,
            "last_result": cleanup_scheduler.last_run,
        },
    }


@app.put("/api/admin/system-settings")
def admin_update_system_settings(payload: SystemSettingsPayload, request: Request, _admin: dict = Depends(admin_user)):
    try:
        settings = system.update(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    task_manager.apply_pool_settings(settings)
    activity_task_manager.apply_pool_settings(settings)
    audit.record(_admin, "system_settings.update", "settings", "system", "更新系统运行参数", request)
    return {
        "settings": settings,
        "pools": {
            "orders": task_manager.pool_stats(),
            "activities": activity_task_manager.pool_stats(),
        },
    }


@app.get("/api/admin/audit-logs")
def admin_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=10, le=200),
    action: str = Query("", max_length=64),
    actor_id: Optional[int] = Query(None),
    keyword: str = Query("", max_length=80),
    start: Optional[str] = Query(None, max_length=32),
    end: Optional[str] = Query(None, max_length=32),
    _admin: dict = Depends(admin_user),
):
    def _parse(v):
        if not v:
            return None
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None

    return audit.query(
        page=page,
        page_size=page_size,
        action=action.strip(),
        actor_id=actor_id,
        keyword=keyword.strip(),
        start=_parse(start),
        end=_parse(end),
    ) | {"actions": audit.actions()}


@app.delete("/api/admin/audit-logs")
def admin_audit_logs_delete(payload: dict, _admin: dict = Depends(admin_user)):
    """批量删除审计日志。"""
    return {"deleted": delete_audit_logs(payload.get("ids") or [])}


@app.get("/api/admin/monitoring")
def admin_monitoring(_admin: dict = Depends(admin_user)):
    return monitoring_snapshot()

@app.get("/api/version")
def read_version(_admin: dict = Depends(admin_user)):
    """当前版本号与更新日志。版本号唯一来源是仓库根目录的 VERSION 文件。"""
    return version_info()


@app.post("/api/admin/maintenance/cleanup")
def admin_run_cleanup(request: Request, _admin: dict = Depends(admin_user)):
    result = run_cleanup_once()
    audit.record(_admin, "maintenance.cleanup", "system", "cleanup", f"手动执行清理：{result}", request)
    return result


@app.post("/api/admin/maintenance/purge")
def admin_run_purge(request: Request, _admin: dict = Depends(admin_user)):
    result = purge_all()
    audit.record(_admin, "maintenance.purge", "system", "purge", f"清空全部历史数据：{result}", request)
    return result


def _start_update_action(action: str, admin: dict, request: Request) -> dict:
    label = "一键更新" if action == "update" else "回滚到上一版本"
    try:
        result = updater.start(action)
    except updater.UpdaterError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    audit.record(admin, f"update.{action}", "system", "updater", f"{label}：已提交执行", request)
    return result


@app.get("/api/admin/update/status")
def admin_update_status(_admin: dict = Depends(admin_user)):
    """运维边车状态：当前版本、落后提交、镜像、回滚点。未启用时回 enabled=False。"""
    try:
        return updater.status()
    except updater.UpdaterError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/admin/update/job")
def admin_update_job(since: int = Query(0, ge=0), _admin: dict = Depends(admin_user)):
    """按 since 增量拉取当前任务的步骤与日志。"""
    try:
        return updater.job(since)
    except updater.UpdaterError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/admin/update", status_code=202)
def admin_update_run(request: Request, admin: dict = Depends(admin_user)):
    """一键更新：备份 → 拉代码 → 拉镜像 → 重启 → 健康检查，全部由边车串行执行。"""
    return _start_update_action("update", admin, request)


@app.post("/api/admin/update/rollback", status_code=202)
def admin_update_rollback(request: Request, admin: dict = Depends(admin_user)):
    """回滚到「上一次在本页执行更新之前」的代码 + 镜像，回滚点是一次性的。"""
    return _start_update_action("rollback", admin, request)
