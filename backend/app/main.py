from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from openpyxl import load_workbook

from app.config import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    HALF_HEADCOST_PATH,
    COOKIE_SECURE,
    INVENTORY_PATH,
    MAX_UPLOAD_BYTES,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    ensure_directories,
)
from app.database import (
    create_user,
    database_status,
    delete_inventory_item,
    ensure_admin_user,
    get_user,
    get_user_by_username,
    list_users,
    update_user_status,
)
from app.schemas import LoginRequest, RegisterRequest, SettingsPayload, SkuQueryRequest
from app.services.auth import admin_user, current_user, hash_password, login_user, make_session, public_user, validate_username
from app.services.half_headcost import delete_entry, load_entries, merge_upload
from app.services.activity_tasks import activity_task_manager
from app.services.inventory import invalidate_cache, inventory_status, load_price_catalog
from app.services.settings import settings_public, update_settings
from app.services.tasks import task_manager


ensure_directories()
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(
    title="销售订单货值/成本计算工具 API",
    version="2.0.0",
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


@app.on_event("startup")
def bootstrap_admin() -> None:
    if ADMIN_PASSWORD:
        ensure_admin_user(ADMIN_USERNAME, hash_password(ADMIN_PASSWORD))
    elif not get_user_by_username(ADMIN_USERNAME):
        logging.getLogger("sales_tool.auth").warning("ADMIN_PASSWORD 未配置，管理员账号尚未创建")


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
def register(request: RegisterRequest):
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
    return {"message": "注册成功，请等待管理员审核"}


@app.post("/api/auth/login")
def login(request: LoginRequest, response: Response):
    user = login_user(request.username, request.password)
    response.set_cookie(SESSION_COOKIE_NAME, make_session(user["id"]), max_age=SESSION_MAX_AGE, httponly=True, samesite="lax", secure=COOKIE_SECURE)
    return public_user(user)


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"message": "已退出登录"}


@app.get("/api/auth/me")
def me(user: dict = Depends(current_user)):
    return public_user(user)


@app.get("/api/status")
def status(user: dict = Depends(current_user)):
    half_count = len(load_entries())
    return {
        "version": app.version,
        "inventory": inventory_status(),
        "half_headcost_count": half_count,
        "tasks": task_manager.list(8, user["id"]),
    }


@app.get("/api/inventory")
def get_inventory_status(_user: dict = Depends(current_user)):
    return inventory_status()


@app.get("/api/inventory/items")
def list_inventory_items(
    query: str = Query("", max_length=80),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=10, le=200),
    user: dict = Depends(current_user),
):
    catalog = load_price_catalog()
    keyword = query.strip().upper()
    items = [
        item
        for sku, item in sorted(catalog.items())
        if not keyword or keyword in sku.upper()
    ]
    start = (page - 1) * page_size
    return {"total": len(items), "items": items[start:start + page_size]}


@app.post("/api/inventory")
async def upload_inventory(file: UploadFile = File(...), _admin: dict = Depends(admin_user)):
    candidate = INVENTORY_PATH.with_name("库存统计表.candidate.xlsx")
    await save_upload(file, candidate)
    try:
        workbook = await run_in_threadpool(
            load_workbook, candidate, read_only=True, data_only=True, keep_links=False
        )
        workbook.close()
    except Exception as exc:
        candidate.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"库存表无法读取：{exc}") from exc
    os.replace(candidate, INVENTORY_PATH)
    invalidate_cache()
    return {"message": "库存表已更新，缓存将在下次查询时自动重建", **inventory_status()}


@app.post("/api/activities/bulk", status_code=202)
async def process_bulk_activity(file: UploadFile = File(...), user: dict = Depends(current_user)):
    validate_excel(file)
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="上传文件超过服务器限制")

    upload_name = file.filename or "报名活动.xlsx"
    job = await run_in_threadpool(activity_task_manager.create, upload_name, user["id"], content)
    return {
        **job,
        "download_url": f"/api/activities/{job['id']}/download",
    }


@app.get("/api/activities")
def list_activity_tasks(limit: int = Query(50, ge=1, le=100), user: dict = Depends(current_user)):
    # Admins can see all activity jobs, including legacy jobs without an owner.
    # Regular users remain strictly scoped to their own records.
    owner_id = None if user["role"] == "admin" else user["id"]
    return {"items": activity_task_manager.list(owner_id, limit)}


@app.get("/api/activities/{job_id}")
def get_activity_task(job_id: str, user: dict = Depends(current_user)):
    job = activity_task_manager.get(job_id, user["id"])
    if not job:
        raise HTTPException(status_code=404, detail="活动任务不存在")
    return job


@app.delete("/api/activities/{job_id}")
def delete_activity_task(job_id: str, user: dict = Depends(current_user)):
    result = activity_task_manager.delete(job_id, user["id"])
    if result == "active":
        raise HTTPException(status_code=409, detail="处理中任务暂不能删除，请等待任务完成")
    if result == "not_found":
        raise HTTPException(status_code=404, detail="活动任务不存在")
    return {"message": "活动任务记录已删除", "id": job_id}


@app.get("/api/activities/{job_id}/download")
def download_activity(job_id: str, user: dict = Depends(current_user)):
    output_path = activity_task_manager.result_path(job_id, None if user["role"] == "admin" else user["id"])
    if not output_path:
        raise HTTPException(status_code=404, detail="处理结果不存在或已过期")
    return FileResponse(
        output_path,
        filename=output_path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.post("/api/inventory/rebuild")
async def rebuild_inventory(_admin: dict = Depends(admin_user)):
    invalidate_cache()
    catalog = await run_in_threadpool(load_price_catalog)
    return {"message": "库存缓存已重建", "sku_count": len(catalog), **inventory_status()}


@app.get("/api/admin/inventory")
def admin_inventory_status(_admin: dict = Depends(admin_user)):
    return inventory_status()


@app.get("/api/admin/inventory/items")
def admin_inventory_items(
    query: str = Query("", max_length=80),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=10, le=200),
    _admin: dict = Depends(admin_user),
):
    catalog = load_price_catalog()
    keyword = query.strip().upper()
    items = [item for sku, item in sorted(catalog.items()) if not keyword or keyword in sku.upper()]
    start = (page - 1) * page_size
    return {"total": len(items), "items": items[start:start + page_size]}


@app.delete("/api/admin/inventory/items/{sku}")
def admin_delete_inventory_item(sku: str, _admin: dict = Depends(admin_user)):
    normalized = sku.strip().upper()
    if not normalized or not delete_inventory_item(normalized):
        raise HTTPException(status_code=404, detail="库存 SKU 不存在")
    return {"message": "库存明细已删除", "sku": normalized, **inventory_status()}


@app.post("/api/skus/query")
async def query_skus(request: SkuQueryRequest, _user: dict = Depends(current_user)):
    normalized = list(dict.fromkeys(sku.strip().upper() for sku in request.skus if sku.strip()))
    if not normalized:
        raise HTTPException(status_code=400, detail="请输入至少一个 SKU")
    catalog = await run_in_threadpool(load_price_catalog)
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
    _user: dict = Depends(current_user),
):
    entries = load_entries()
    keyword = query.strip().upper()
    items = [
        {"sku": sku, "set_type": set_type}
        for sku, set_type in sorted(entries.items())
        if not keyword or keyword in sku.upper()
    ]
    start = (page - 1) * page_size
    return {"total": len(items), "items": items[start:start + page_size]}


@app.post("/api/half-headcost/import")
async def import_half_headcost(file: UploadFile = File(...), _admin: dict = Depends(admin_user)):
    validate_excel(file)
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="上传文件超过服务器限制")
    try:
        result = await run_in_threadpool(merge_upload, content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "头程减半名单已合并", **result}


@app.delete("/api/half-headcost/{sku}")
def remove_half_headcost(sku: str, _admin: dict = Depends(admin_user)):
    normalized = sku.strip().upper()
    if not delete_entry(normalized):
        raise HTTPException(status_code=404, detail="SKU 不在头程减半名单中")
    return {"message": "已删除", "sku": normalized}


@app.post("/api/tasks", status_code=202)
async def create_task(
    sales: UploadFile = File(...),
    delivery: UploadFile = File(...),
    half_headcost: Optional[UploadFile] = File(None),
    user: dict = Depends(current_user),
):
    if not INVENTORY_PATH.exists():
        raise HTTPException(status_code=409, detail="服务器尚未配置库存统计表")
    validate_excel(sales)
    validate_excel(delivery)
    if half_headcost:
        validate_excel(half_headcost)
    task = task_manager.create(
        sales.filename or "销售订单.xlsx",
        delivery.filename or "派送订单.xlsx",
        half_headcost.filename if half_headcost else None,
        user["id"],
    )
    try:
        await save_upload(sales, task_manager.file_path(task["id"], "sales"))
        await save_upload(delivery, task_manager.file_path(task["id"], "delivery"))
        if half_headcost:
            await save_upload(half_headcost, task_manager.file_path(task["id"], "half_headcost"))
    except Exception:
        raise
    task_manager.queue(task["id"])
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
def delete_task(task_id: str, user: dict = Depends(current_user)):
    result = task_manager.delete(task_id, user["id"])
    if result == "active":
        raise HTTPException(status_code=409, detail="处理中任务暂不能删除，请等待任务完成")
    if result == "not_found":
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"message": "任务记录已删除", "id": task_id}


@app.get("/api/tasks/{task_id}/download")
def download_task(task_id: str, user: dict = Depends(current_user)):
    result = task_manager.result_path(task_id, None if user["role"] == "admin" else user["id"])
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


@app.get("/api/admin/users")
def admin_users(_admin: dict = Depends(admin_user)):
    return {"items": list_users()}


@app.post("/api/admin/users/{user_id}/approve")
def approve_user(user_id: int, _admin: dict = Depends(admin_user)):
    target = get_user(user_id)
    if not target or target["role"] == "admin":
        raise HTTPException(status_code=404, detail="普通用户不存在")
    user = update_user_status(user_id, "approved")
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return public_user(user)


@app.post("/api/admin/users/{user_id}/reject")
def reject_user(user_id: int, _admin: dict = Depends(admin_user)):
    target = get_user(user_id)
    if not target or target["role"] == "admin":
        raise HTTPException(status_code=404, detail="普通用户不存在")
    user = update_user_status(user_id, "rejected")
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return public_user(user)


@app.get("/api/admin/settings")
def admin_get_settings(_admin: dict = Depends(admin_user)):
    return settings_public()


@app.put("/api/admin/settings")
def admin_update_settings(payload: SettingsPayload, _admin: dict = Depends(admin_user)):
    try:
        return update_settings(payload.model_dump())
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
