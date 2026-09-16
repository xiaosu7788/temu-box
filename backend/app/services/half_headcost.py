from __future__ import annotations

import io
import re
import threading
from pathlib import Path
from typing import Dict, Union

from openpyxl import load_workbook

from app.config import HALF_HEADCOST_SEED_PATH
from app.database import (
    delete_half_entry,
    load_half_entries,
    merge_half_entries,
    upsert_half_entry,
)
from app.services.inventory import CODE_RE, extract_set_type


SKU_HEADER_RE = re.compile(
    r"(sku|货号|商品编号|商品编码|产品编号|产品编码)", re.IGNORECASE
)
_LOCK = threading.Lock()


def extract_sku_types(source: Union[Path, bytes, bytearray]) -> Dict[str, str]:
    if isinstance(source, (bytes, bytearray)):
        wb = load_workbook(io.BytesIO(source), read_only=True, data_only=True, keep_links=False)
    else:
        wb = load_workbook(source, read_only=True, data_only=True, keep_links=False)
    sku_types = {}
    try:
        for ws in wb.worksheets:
            sku_columns = set()
            for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 20), values_only=True):
                for index, value in enumerate(row):
                    if value is not None and SKU_HEADER_RE.search(str(value)):
                        sku_columns.add(index)
            for row in ws.iter_rows(values_only=True):
                texts = [
                    str(value) for value in row
                    if value is not None and not str(value).startswith("=DISPIMG")
                ]
                set_type = extract_set_type(texts) or "单品"
                values = [row[index] for index in sku_columns if index < len(row)] if sku_columns else row
                for value in values:
                    if value is None or str(value).startswith("="):
                        continue
                    for sku in CODE_RE.findall(str(value).strip()):
                        # 统一大写：与接口层 save_entry/delete_entry 的 upper() 保持一致
                        sku_types[sku.upper()] = set_type
    finally:
        wb.close()
    return sku_types


def load_entries(category_id: int, inventory_category: str = "A") -> Dict[str, str]:
    """读取业务品类 + 库存类目的头程减半名单。

    首次访问时用内置种子文件初始化一次，并用标记位记录「已初始化」，
    避免用户把名单清空后种子被反复重新导入（删除操作被静默撤销）。
    """
    with _LOCK:
        values = load_half_entries(category_id, inventory_category)
        if inventory_category != "A" or not HALF_HEADCOST_SEED_PATH.exists():
            return values
        from app.services.categories import default_category_id
        if category_id != default_category_id():
            return values
        from app.database import get_flag, set_flag
        seed_flag = f"half_headcost_seed_initialized_{category_id}_{inventory_category}"
        if get_flag(seed_flag):
            return values
        seeds = extract_sku_types(HALF_HEADCOST_SEED_PATH)
        if seeds:
            merge_half_entries(category_id, seeds, inventory_category)
            values = load_half_entries(category_id, inventory_category)
        set_flag(seed_flag)
        return values


def merge_upload(source: Union[Path, bytes, bytearray], category_id: int, inventory_category: str = "A") -> dict:
    incoming = extract_sku_types(source)
    if not incoming:
        raise ValueError("上传表格中未识别到 SKU")
    with _LOCK:
        added, total = merge_half_entries(category_id, incoming, inventory_category)
    return {"incoming": len(incoming), "added": added, "total": total}


def entry_exists(sku: str, category_id: int, inventory_category: str = "A") -> bool:
    """判断某条 SKU 是否已在指定品类 + 库存类目的头程减半名单中。"""
    with _LOCK:
        return (sku or "").strip().upper() in load_half_entries(category_id, inventory_category)


def save_entry(sku: str, set_type: str, category_id: int, inventory_category: str = "A") -> dict:
    """单条新增/覆盖头程减半名单条目，返回该条目。"""
    normalized_sku = (sku or "").strip().upper()
    normalized_type = (set_type or "").strip()
    if not normalized_sku:
        raise ValueError("SKU 不能为空")
    if not normalized_type:
        raise ValueError("类型不能为空")
    with _LOCK:
        return upsert_half_entry(category_id, normalized_sku, normalized_type, inventory_category)


def delete_entry(sku: str, category_id: int, inventory_category: str = "A") -> bool:
    with _LOCK:
        return delete_half_entry(category_id, sku, inventory_category)
