from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from openpyxl import load_workbook

from app.config import (
    HALF_HEADCOST_PATH,
    ACTIVITY_DIR,
    INVENTORY_PATH,
    MAX_UPLOAD_BYTES,
    ensure_directories,
)
from app.database import database_status, save_activity_job
from app.schemas import SkuQueryRequest
from app.services.half_headcost import delete_entry, load_entries, merge_upload
from app.services.activity import process_activity_workbook
from app.services.inventory import invalidate_cache, inventory_status, load_price_catalog
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


@app.get("/api/status")
def status():
    half_count = len(load_entries())
    return {
        "version": app.version,
        "inventory": inventory_status(),
        "half_headcost_count": half_count,
        "tasks": task_manager.list(8),
    }


@app.get("/api/inventory")
def get_inventory_status():
    return inventory_status()


@app.get("/api/inventory/items")
def list_inventory_items(
    query: str = Query("", max_length=80),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=10, le=200),
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
async def upload_inventory(file: UploadFile = File(...)):
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


@app.post("/api/activities/bulk")
async def process_bulk_activity(file: UploadFile = File(...)):
    validate_excel(file)
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="上传文件超过服务器限制")

    job_id = uuid.uuid4().hex
    output_dir = ACTIVITY_DIR / job_id
    output_path = output_dir / "批量报名活动处理结果.xlsx"
    upload_name = file.filename or "报名活动.xlsx"
    save_activity_job(job_id, upload_name, None, {}, status="running")
    try:
        stats = await run_in_threadpool(process_activity_workbook, content, output_path)
    except ValueError as exc:
        save_activity_job(job_id, upload_name, None, {"error": str(exc)}, status="failed")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        save_activity_job(job_id, upload_name, None, {"error": str(exc)}, status="failed")
        raise HTTPException(status_code=400, detail=f"报名表处理失败：{exc}") from exc
    save_activity_job(job_id, upload_name, str(output_path), stats)
    return {
        "message": "批量报名活动处理完成",
        "job_id": job_id,
        "filename": output_path.name,
        "download_url": f"/api/activities/{job_id}/download",
        "stats": stats,
    }


@app.get("/api/activities/{job_id}/download")
def download_activity(job_id: str):
    output_path = ACTIVITY_DIR / job_id / "批量报名活动处理结果.xlsx"
    if not output_path.is_file():
        raise HTTPException(status_code=404, detail="处理结果不存在或已过期")
    return FileResponse(
        output_path,
        filename=output_path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.post("/api/inventory/rebuild")
async def rebuild_inventory():
    invalidate_cache()
    catalog = await run_in_threadpool(load_price_catalog)
    return {"message": "库存缓存已重建", "sku_count": len(catalog), **inventory_status()}


@app.post("/api/skus/query")
async def query_skus(request: SkuQueryRequest):
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
async def import_half_headcost(file: UploadFile = File(...)):
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
def remove_half_headcost(sku: str):
    normalized = sku.strip().upper()
    if not delete_entry(normalized):
        raise HTTPException(status_code=404, detail="SKU 不在头程减半名单中")
    return {"message": "已删除", "sku": normalized}


@app.post("/api/tasks", status_code=202)
async def create_task(
    sales: UploadFile = File(...),
    delivery: UploadFile = File(...),
    half_headcost: Optional[UploadFile] = File(None),
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
    )
    try:
        await save_upload(sales, task_manager.file_path(task["id"], "sales"))
        await save_upload(delivery, task_manager.file_path(task["id"], "delivery"))
        if half_headcost:
            await save_upload(half_headcost, task_manager.file_path(task["id"], "half_headcost"))
    except Exception:
        raise
    task_manager.queue(task["id"])
    return task_manager.get(task["id"])


@app.get("/api/tasks")
def list_tasks(limit: int = Query(30, ge=1, le=100)):
    return {"items": task_manager.list(limit)}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    task = task_manager.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@app.get("/api/tasks/{task_id}/download")
def download_task(task_id: str):
    result = task_manager.result_path(task_id)
    if not result:
        raise HTTPException(status_code=404, detail="结果文件尚未生成")
    return FileResponse(
        result,
        filename="销售订单汇总表.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
