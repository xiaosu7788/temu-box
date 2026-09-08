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
                        sku_types[sku] = set_type
    finally:
        wb.close()
    return sku_types


def load_entries(category_id: int) -> Dict[str, str]:
    """读取品类的头程减半名单；默认品类为空且存在初始名单文件时自动种子。"""
    with _LOCK:
        values = load_half_entries(category_id)
        if not values and HALF_HEADCOST_SEED_PATH.exists():
            from app.services.categories import default_category_id

            if category_id == default_category_id():
                seeds = extract_sku_types(HALF_HEADCOST_SEED_PATH)
                merge_half_entries(category_id, seeds)
                values = load_half_entries(category_id)
        return values


def merge_upload(source: Union[Path, bytes, bytearray], category_id: int) -> dict:
    incoming = extract_sku_types(source)
    if not incoming:
        raise ValueError("上传表格中未识别到 MB131- 开头的 SKU")
    with _LOCK:
        added, total = merge_half_entries(category_id, incoming)
    return {
        "incoming": len(incoming),
        "added": added,
        "total": total,
    }


def delete_entry(sku: str, category_id: int) -> bool:
    with _LOCK:
        return delete_half_entry(category_id, sku)
