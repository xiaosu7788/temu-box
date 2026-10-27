from __future__ import annotations

import io
import re
import threading
from typing import Dict, List, Optional, Tuple, Union

from openpyxl import load_workbook

from app.database import (
    delete_weight_entry,
    load_weight_entries,
    merge_weight_entries,
    upsert_weight_entry,
)


# 头程单价：每克多少钱
HEADCOST_PER_GRAM = 0.017
# 克重表头关键字（多段表格里写法不统一，统一按包含「克重」识别）
WEIGHT_HEADER_RE = re.compile(r"克重", re.IGNORECASE)
# 编号列候选表头：编码 / 商品编码 / 编号 / 标签 / SKU 等
CODE_HEADER_RE = re.compile(r"(编码|编号|货号|标签|sku|商品编号|商品编码|产品编号|产品编码)", re.IGNORECASE)
# 通用编码（如 MB131-260 / MB131-C010）；样例表里 243 个编码全部是 MB131 前缀
CODE_RE = re.compile(r"[A-Za-z]{2,}[A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+")
# 克重数值：99g / 99 克 / 160.3 / 1.2kg
WEIGHT_VALUE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|千克|公斤|g|克)?", re.IGNORECASE)

_LOCK = threading.Lock()


def headcost_for_weight(weight_g: float) -> float:
    """克重 → 头程：克重 × 0.017，保留两位。"""
    return round(float(weight_g or 0) * HEADCOST_PER_GRAM, 2)


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _parse_weight(value: object) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    match = WEIGHT_VALUE_RE.search(_text(value))
    if not match:
        return None
    try:
        weight = float(match.group(1))
    except ValueError:
        return None
    unit = (match.group(2) or "").lower()
    if unit in {"kg", "千克", "公斤"}:
        weight *= 1000
    return weight if weight > 0 else None


def _codes_in(value: object) -> List[str]:
    """从单元格里提编码；跳过公式引用（WPS 的 DISPIMG 图片公式会带出 ID_ 假编码）。"""
    text = _text(value)
    if not text or text.startswith("=") or "DISPIMG" in text.upper():
        return []
    return CODE_RE.findall(text)


def _is_header_row(row: Tuple) -> bool:
    """表头行：同一行里既有克重列，也有编号列。"""
    texts = [_text(value) for value in row]
    has_weight = any(WEIGHT_HEADER_RE.search(text) for text in texts)
    has_code = any(CODE_HEADER_RE.search(text) for text in texts)
    return has_weight and has_code


def extract_weight_entries(source: Union[bytes, bytearray, str]) -> Dict[str, float]:
    """从参考表提取「编号 → 克重(g)。

    这类参考表常见多段表头（同一 sheet 里重复出现表头，每段列位置不同），
    所以按「遇到表头就切换当前段的列映射」处理，逐段解析到下一个表头为止。
    编号列优先取表头含 编码/编号/货号/标签 的列，取不到再按段内编码密度推断。
    """
    wb = load_workbook(io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source, read_only=True, data_only=True, keep_links=False)
    entries: Dict[str, float] = {}
    try:
        for ws in wb.worksheets:
            weight_column: Optional[int] = None
            code_columns: List[int] = []
            for row in ws.iter_rows(values_only=True):
                texts = [_text(value) for value in row]
                if _is_header_row(row):
                    weight_column = next(
                        (index for index, text in enumerate(texts) if WEIGHT_HEADER_RE.search(text)),
                        None,
                    )
                    code_columns = [
                        index for index, text in enumerate(texts)
                        if CODE_HEADER_RE.search(text) and not WEIGHT_HEADER_RE.search(text)
                    ]
                    continue
                if weight_column is None:
                    continue
                weight = _parse_weight(row[weight_column] if weight_column < len(row) else None)
                if weight is None:
                    continue
                codes = []
                for index in code_columns:
                    if index < len(row):
                        codes.extend(_codes_in(row[index]))
                if not codes:
                    # 表头没写好时兜底：整行找编码
                    codes = [code for value in row for code in _codes_in(value)]
                for code in codes:
                    entries[code.strip().upper()] = weight
    finally:
        wb.close()
    return entries


def merge_upload(source: Union[bytes, bytearray, str], category_id: int, inventory_category: str = "A") -> dict:
    incoming = extract_weight_entries(source)
    if not incoming:
        raise ValueError("上传表格中未识别到编号与克重，请确认表头包含「克重」且有对应编号列")
    with _LOCK:
        added, total = merge_weight_entries(category_id, incoming, inventory_category)
    return {"incoming": len(incoming), "added": added, "total": total, "updated": len(incoming) - added}


def load_entries(category_id: int, inventory_category: str = "A") -> Dict[str, float]:
    with _LOCK:
        return load_weight_entries(category_id, inventory_category)


def entry_exists(sku: str, category_id: int, inventory_category: str = "A") -> bool:
    with _LOCK:
        return _text(sku).upper() in load_weight_entries(category_id, inventory_category)


def save_entry(sku: str, weight_g: float, category_id: int, inventory_category: str = "A") -> dict:
    normalized = _text(sku).upper()
    if not normalized:
        raise ValueError("编号不能为空")
    if weight_g is None or float(weight_g) <= 0:
        raise ValueError("克重必须大于 0")
    with _LOCK:
        return upsert_weight_entry(category_id, normalized, float(weight_g), inventory_category)


def delete_entry(sku: str, category_id: int, inventory_category: str = "A") -> bool:
    with _LOCK:
        return delete_weight_entry(category_id, sku, inventory_category)
