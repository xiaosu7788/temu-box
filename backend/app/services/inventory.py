from __future__ import annotations

import io
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union

from openpyxl import load_workbook

from app.config import DATA_DIR, INVENTORY_PATH, PRICE_CACHE_PATH
from app.database import (
    current_inventory_metadata,
    get_inventory_catalog,
    invalidate_inventory_catalog,
    inventory_signature_matches,
    save_inventory_catalog,
)


LogFn = Optional[Callable[[str], None]]
WorkbookInput = Union[str, Path, bytes, bytearray]

CODE_RE = re.compile(r"MB131-[A-Za-z0-9]+")
PRICE_HEADER_RE = re.compile(r"(价格|货值|单价|售价|成本价|price)", re.IGNORECASE)
# 件数匹配：中文数字 + 阿拉伯数字，用前后界锚定避免 "15件套" 被切成 "5件套"
SET_RE = re.compile(
    r"(?<![0-9])(?P<num>[0-9]{1,2}|[一二三四五六七八九十]{1,3})\s*件套"
)
SET_NUM_MAP = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15,
    "十六": 16, "十七": 17, "十八": 18, "十九": 19, "二十": 20,
    "二十一": 21, "二十二": 22, "二十三": 23, "二十四": 24, "二十五": 25,
    "二十六": 26, "二十七": 27, "二十八": 28, "二十九": 29, "三十": 30,
}

EXTRACTION_PROFILES: Dict[str, dict] = {
    "A": {
        "label": "A类目",
        "code_pattern": r"MB131-[A-Za-z0-9]+",
        "sheet_rules": {
            "Sheet1": {3: 6, 10: 8},
            "美东": {3: 5, 5: 3, 8: 9, 9: 8},
            "加拿大1仓": {10: 8, 6: 3},
            "加拿大2仓（美国S仓）": {10: 8, 3: 6},
            "美西2仓": {8: 9, 4: 3},
            "澳-日-英": {12: 10},
            "内衣": {5: 3},
            "cos，丝袜": {4: 5},
            "玩具": {4: 6},
            "个人单品": {3: 6},
            "库存0": {3: 6, 4: 5, 5: 3, 6: 8, 7: 6, 8: 9, 9: 7, 10: 8},
        },
        "priority": [
            "Sheet1", "美东", "加拿大1仓", "加拿大2仓（美国S仓）", "美西2仓",
            "澳-日-英", "内衣", "cos，丝袜", "玩具", "个人单品", "库存0",
        ],
        "price_max": 100.0,
    },
    "B": {
        "label": "B类目",
        # B 表格式待实际样表确定后细化；先用通用编码模式
        "code_pattern": r"[A-Z]{2,6}-[A-Za-z0-9]+",
        # 空映射 = 自动探测模式：扫描所有 sheet，SKU 列为含编码文本的列，价格列在其右侧就近查找
        "sheet_rules": {},
        "priority": [],
        "price_max": 100.0,
    },
}

# 兼容旧引用：A 类目的列映射与优先级
SHEET_RULES = EXTRACTION_PROFILES["A"]["sheet_rules"]
PRIORITY = EXTRACTION_PROFILES["A"]["priority"]
CODE_RE = re.compile(EXTRACTION_PROFILES["A"]["code_pattern"])


def resolve_profile(category: Optional[str]) -> dict:
    """按类目代号取提取配置。

    未注册（或已停用/已删除）的类目**直接报错**，不再静默回退到 A：
    回退会让「向不存在的类目上传库存表」变成「覆盖 A 类目的库存表」，
    也会让读操作拿到 A 的价格而不报错，属于静默数据损坏。
    """
    requested = (category or "A").strip().upper()
    key = requested
    profile = dict(EXTRACTION_PROFILES.get(key, EXTRACTION_PROFILES["B"]))
    definition = None
    try:
        from app.database import get_inventory_category_defs
        definition = get_inventory_category_defs().get(key)
    except Exception:
        pass
    if key not in EXTRACTION_PROFILES and definition is None:
        raise ValueError(f"库存类目不存在或已停用：{requested}")
    if key not in EXTRACTION_PROFILES:
        profile["label"] = definition.get("label", f"{key}类目")
        profile["sheet_rules"] = {}
        profile["priority"] = []
    if definition:
        profile.update({k: definition[k] for k in ("label", "code_pattern", "price_max") if definition.get(k) is not None})
    return {"key": key, **profile}


def inventory_categories() -> List[dict]:
    """库存类目列表（供前端选择器）：内置 + 数据库定义。"""
    items: List[dict] = []
    seen = set()
    try:
        from app.database import get_inventory_category_defs
        for key, definition in get_inventory_category_defs().items():
            items.append({"key": key, **definition})
            seen.add(key)
    except Exception:
        pass
    for key, profile in EXTRACTION_PROFILES.items():
        if key not in seen:
            items.append({"key": key, "label": profile["label"], "mode": "mapped" if key == "A" else "auto"})
    return items


def inventory_path_for(category: Optional[str] = "A") -> Path:
    key = resolve_profile(category)["key"]
    if key == "A":
        return INVENTORY_PATH
    return INVENTORY_PATH.with_name(f"库存统计表_{key}.xlsx")


def price_cache_path_for(category: Optional[str] = "A") -> Path:
    key = resolve_profile(category)["key"]
    if key == "A":
        return PRICE_CACHE_PATH
    return PRICE_CACHE_PATH.with_name(f"price_cache_{key}.json")


PRICE_CACHE_VERSION = 4
_CACHE_LOCK = threading.Lock()


def _log(log: LogFn, message: str) -> None:
    if log:
        log(message)


def to_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def to_price(value: object, price_max: float = 100.0) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text or text.startswith("=DISPIMG") or CODE_RE.search(text):
            return None
        text = text.replace(",", "")
        match = re.fullmatch(r"[￥¥$€]?\s*(\d+(?:\.\d+)?)\s*(?:元|块)?", text)
        if not match:
            return None
        number = float(match.group(1))
    else:
        number = to_float(value)
    if number is None or not 0 < number <= price_max:
        return None
    return number


def extract_set_type(row_texts) -> Optional[str]:
    for text in row_texts:
        if not text:
            continue
        for match in SET_RE.finditer(str(text)):
            raw = match.group("num")
            # 阿拉伯数字直接转；中文数字查表；超出 2-30 视为无效（与品类档位上限一致）
            number = int(raw) if raw.isdigit() else SET_NUM_MAP.get(raw)
            if number and 2 <= number <= 30:
                return f"{number}件套"
        if re.search(r"单品", str(text)):
            return "单品"
        if re.search(r"多件套|套装|组合", str(text)):
            return "多件套"
    return None


def build_price_header_index(ws, max_col: int = 20):
    """Record the nearest price header group above every worksheet row."""
    limit = min(ws.max_column, max_col)
    index = [set() for _ in range(ws.max_row + 1)]
    active_columns = set()
    for row in range(1, ws.max_row + 1):
        index[row] = set(active_columns)
        row_columns = set()
        for column in range(1, limit + 1):
            value = ws.cell(row, column).value
            if value is not None and PRICE_HEADER_RE.search(str(value).strip()):
                row_columns.add(column)
        if row_columns:
            active_columns = row_columns
    return index


def find_row_price(
    ws,
    row: int,
    mapped_price_col: int,
    sku_col: Optional[int] = None,
    price_header_cols=None,
    max_col: int = 20,
    price_max: float = 100.0,
) -> Tuple[Optional[float], Optional[int]]:
    """Find price by nearest header, legacy mapping, then expanding around SKU."""
    limit = min(ws.max_column, max_col)
    if price_header_cols:
        header_col = min(
            (col for col in price_header_cols if col <= limit),
            key=lambda col: (abs(col - sku_col), col) if sku_col else (col, col),
            default=None,
        )
        if header_col is not None:
            price = to_price(ws.cell(row, header_col).value, price_max)
            if price is not None:
                return price, header_col

    price = to_price(ws.cell(row, mapped_price_col).value, price_max)
    if price is not None:
        return price, mapped_price_col

    if sku_col is not None:
        for distance in range(1, limit + 1):
            for column in (sku_col - distance, sku_col + distance):
                if column < 1 or column > limit or column == mapped_price_col:
                    continue
                price = to_price(ws.cell(row, column).value, price_max)
                if price is not None:
                    return price, column
    return None, None


def _scan_sheet_auto(
    ws,
    sheet_name: str,
    code_re,
    price_max: float,
    candidates: Dict[str, list],
    priority: int,
) -> None:
    """自动探测模式：无列映射时扫描 sheet，SKU 列 = 含编码的列，价格列在其右侧就近查找。"""
    limit = min(ws.max_column, 30)
    for row in range(1, ws.max_row + 1):
        for sku_col in range(1, limit + 1):
            value = ws.cell(row, sku_col).value
            if not isinstance(value, str):
                continue
            codes = code_re.findall(value.strip())
            if not codes:
                continue
            row_texts = [
                str(ws.cell(row, col).value)
                for col in range(1, limit + 1)
                if ws.cell(row, col).value is not None
                and not str(ws.cell(row, col).value).startswith("=DISPIMG")
            ]
            set_type = extract_set_type(row_texts) or "单品"
            price, price_col = None, None
            # 先向右找价格（优先），再向左一格
            for column in range(sku_col + 1, limit + 1):
                price = to_price(ws.cell(row, column).value, price_max)
                if price is not None:
                    price_col = column
                    break
            if price is None and sku_col > 1:
                price = to_price(ws.cell(row, sku_col - 1).value, price_max)
                if price is not None:
                    price_col = sku_col - 1
            if price is None:
                continue
            for code in codes:
                # SKU 统一大写入库：接口层（编辑/删除/保留旧值）都用 upper() 比较，
                # 否则原文大小写的键会导致「明明在列表里却 404 / 勾选无效」。
                code = code.upper()
                candidates.setdefault(code, []).append({
                    "sku": code,
                    "price": price,
                    "set_type": set_type,
                    "source_sheet": sheet_name,
                    "source_row": row,
                    "source_column": price_col,
                    "priority": priority,
                })


def build_price_catalog(source: WorkbookInput, log: LogFn = None, category: Optional[str] = "A") -> Dict[str, dict]:
    profile = resolve_profile(category)
    code_re = re.compile(profile["code_pattern"])
    price_max = float(profile.get("price_max", 100.0))
    sheet_rules: dict = profile["sheet_rules"]
    priority_names: list = profile["priority"]

    if isinstance(source, (bytes, bytearray)):
        wb = load_workbook(io.BytesIO(source), data_only=True, keep_links=False)
    else:
        wb = load_workbook(source, data_only=True, keep_links=False)

    candidates: Dict[str, list] = {}
    try:
        if sheet_rules:
            # 固定列映射模式（A 类目既有逻辑）
            for priority, sheet_name in enumerate(priority_names):
                if sheet_name not in wb.sheetnames:
                    continue
                ws = wb[sheet_name]
                rules = sheet_rules.get(sheet_name, {})
                if not rules:
                    continue
                header_index = build_price_header_index(ws)
                for row in range(1, ws.max_row + 1):
                    if not any(
                        isinstance(ws.cell(row, col).value, str)
                        and code_re.search(ws.cell(row, col).value)
                        for col in rules
                    ):
                        continue

                    row_texts = None
                    set_type = None
                    for sku_col, mapped_price_col in rules.items():
                        value = ws.cell(row, sku_col).value
                        if not isinstance(value, str):
                            continue
                        cell_text = value.strip()
                        codes = code_re.findall(cell_text)
                        if not codes:
                            continue
                        if row_texts is None:
                            row_texts = [
                                str(ws.cell(row, col).value)
                                for col in range(1, min(ws.max_column, 20) + 1)
                                if ws.cell(row, col).value is not None
                                and not str(ws.cell(row, col).value).startswith("=DISPIMG")
                            ]
                            set_type = extract_set_type(row_texts) or "单品"
                        price, actual_price_col = find_row_price(
                            ws, row, mapped_price_col, sku_col, header_index[row], price_max=price_max
                        )
                        if price is None:
                            continue
                        for code in codes:
                            code = code.upper()  # 与接口层的 upper() 比较保持一致
                            candidates.setdefault(code, []).append({
                                "sku": code,
                                "price": price,
                                "set_type": set_type,
                                "source_sheet": sheet_name,
                                "source_row": row,
                                "source_column": actual_price_col,
                                "priority": priority,
                            })
        else:
            # 自动探测模式（B 类目等未配置列映射的类目）
            for priority, sheet_name in enumerate(wb.sheetnames):
                if sheet_name.startswith("WpsReserved"):
                    continue
                _scan_sheet_auto(wb[sheet_name], sheet_name, code_re, price_max, candidates, priority)
    finally:
        wb.close()

    catalog = {}
    for sku, sku_candidates in candidates.items():
        catalog[sku] = min(
            sku_candidates,
            key=lambda item: (item["price"], item["priority"]),
        )
        catalog[sku].pop("priority", None)
    _log(log, f"[{profile['label']}] 库存扫描完成，共提取 {len(catalog)} 个 SKU，重复 SKU 取最低货值。")
    return catalog


def inventory_signature(path: Path = INVENTORY_PATH) -> dict:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "parser_version": PRICE_CACHE_VERSION,
    }


def invalidate_cache(category: Optional[str] = "A") -> None:
    key = resolve_profile(category)["key"]
    invalidate_inventory_catalog(key)
    try:
        price_cache_path_for(key).unlink()
    except FileNotFoundError:
        pass


def load_price_catalog(path: Optional[Path] = None, log: LogFn = None, category: Optional[str] = "A") -> Dict[str, dict]:
    key = resolve_profile(category)["key"]
    inventory_path = path or inventory_path_for(key)
    cache_path = price_cache_path_for(key)
    if not inventory_path.exists():
        catalog = get_inventory_catalog(key) if path is None else {}
        if catalog:
            _log(log, f"[{key}类目] 库存原始表暂不可用，使用数据库库存数据，共 {len(catalog)} 个 SKU。")
            return catalog
        # A 类目沿用历史行为（表不存在报错）；其他类目首次使用时返回空
        if key == "A":
            raise FileNotFoundError(f"库存统计表不存在：{inventory_path}")
        _log(log, f"[{key}类目] 库存表尚未上传，返回空库存。")
        return {}
    signature = inventory_signature(inventory_path)
    with _CACHE_LOCK:
        if path is None and inventory_signature_matches(signature, key):
            catalog = get_inventory_catalog(key)
            _log(log, f"[{key}类目] 使用数据库库存数据，共 {len(catalog)} 个 SKU。")
            return catalog
        try:
            with cache_path.open("r", encoding="utf-8") as handle:
                cached = json.load(handle)
            if cached.get("signature") == signature:
                catalog = cached.get("catalog", {})
                if path is None and catalog:
                    save_inventory_catalog(signature, catalog, key)
                _log(log, f"[{key}类目] 使用库存缓存，共 {len(catalog)} 个 SKU。")
                return catalog
        except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
            pass

        started = time.time()
        _log(log, f"[{key}类目] 库存缓存无效，正在重新扫描库存统计表...")
        catalog = build_price_catalog(inventory_path, log, key)
        if path is None:
            save_inventory_catalog(signature, catalog, key)
        payload = {
            "signature": signature,
            "catalog": catalog,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(temp_path, cache_path)
        _log(log, f"[{key}类目] 库存缓存已更新，耗时 {time.time() - started:.1f} 秒。")
        return catalog


def inventory_status(category: Optional[str] = "A") -> dict:
    key = resolve_profile(category)["key"]
    inventory_path = inventory_path_for(key)
    cache_path = price_cache_path_for(key)
    metadata = current_inventory_metadata(key)
    status = {
        "inventory_category": key,
        "path": str(inventory_path),
        "exists": inventory_path.exists(),
        "cache_exists": cache_path.exists(),
        "legacy_cache_valid": False,
        "sku_count": metadata["sku_count"] if metadata else 0,
        "size": None,
        "modified_at": None,
        "cache_valid": bool(metadata and inventory_path.exists() and inventory_signature(inventory_path) == {key2: metadata[key2] for key2 in ("path", "size", "mtime_ns", "parser_version")}),
        "database": {
            "configured": True,
            "has_inventory": metadata is not None,
        },
    }
    if inventory_path.exists():
        stat = inventory_path.stat()
        status["size"] = stat.st_size
        status["modified_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
    try:
        with cache_path.open("r", encoding="utf-8") as handle:
            cached = json.load(handle)
        status["legacy_cache_valid"] = (
            inventory_path.exists()
            and cached.get("signature") == inventory_signature(inventory_path)
        )
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        pass
    return status

def _pending_paths(category: str) -> Tuple[Path, Path]:
    """按类目返回 (暂存 Excel, 暂存解析结果 JSON) 路径。"""
    key = resolve_profile(category)["key"]
    if key == "A":
        upload = Path(os.environ.get("PENDING_UPLOAD_PATH", DATA_DIR / "pending_inventory.xlsx"))
        catalog = Path(os.environ.get("PENDING_CATALOG_PATH", DATA_DIR / "pending_inventory_catalog.json"))
    else:
        upload = DATA_DIR / f"pending_inventory_{key}.xlsx"
        catalog = DATA_DIR / f"pending_inventory_{key}_catalog.json"
    return upload, catalog


_PENDING_LOCK = threading.Lock()
_pending_states: Dict[str, dict] = {}


def _save_pending_catalog(catalog: Dict[str, dict], category: str) -> None:
    _, catalog_path = _pending_paths(category)
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = catalog_path.with_suffix(catalog_path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(catalog, handle, ensure_ascii=False)
    os.replace(temp_path, catalog_path)


def _load_pending_catalog(category: str) -> Optional[Dict[str, dict]]:
    _, catalog_path = _pending_paths(category)
    try:
        with catalog_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _clear_pending_files(category: str) -> None:
    upload_path, catalog_path = _pending_paths(category)
    upload_path.unlink(missing_ok=True)
    catalog_path.unlink(missing_ok=True)


def _pending_payload(catalog: dict, diff: dict, category: str) -> dict:
    return {
        "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sku_count": len(catalog),
        "diff": diff,
        "inventory_category": category,
    }


def _items_equal(a: dict, b: dict) -> bool:
    for key in ("price", "set_type"):
        if a.get(key) != b.get(key):
            return False
    return True


def _preview_item(item: dict) -> dict:
    return {key: item.get(key) for key in ("price", "set_type", "source_sheet", "source_row", "source_column")}


def build_inventory_diff(old_catalog: dict, new_catalog: dict) -> dict:
    changed, added, removed, unchanged = [], [], [], []
    old_skus, new_skus = set(old_catalog), set(new_catalog)
    for sku in sorted(old_skus & new_skus):
        old_item, new_item = old_catalog[sku], new_catalog[sku]
        if _items_equal(old_item, new_item):
            unchanged.append(sku)
        else:
            changed.append({
                "sku": sku,
                "old": _preview_item(old_item),
                "new": _preview_item(new_item),
            })
    for sku in sorted(new_skus - old_skus):
        added.append({"sku": sku, "new": _preview_item(new_catalog[sku])})
    for sku in sorted(old_skus - new_skus):
        removed.append({"sku": sku, "old": _preview_item(old_catalog[sku])})
    return {"changed": changed, "added": added, "removed": removed, "unchanged": unchanged}


def pending_upload(category: Optional[str] = "A") -> Optional[dict]:
    key = resolve_profile(category)["key"]
    upload_path, _ = _pending_paths(key)
    with _PENDING_LOCK:
        if not upload_path.exists():
            _pending_states.pop(key, None)
            return None
        if key not in _pending_states:
            catalog = _load_pending_catalog(key)
            if catalog is None:
                catalog = build_price_catalog(upload_path, category=key)
            old_catalog = get_inventory_catalog(key)
            _pending_states[key] = _pending_payload(catalog, build_inventory_diff(old_catalog, catalog), key)
        return dict(_pending_states[key])


def all_pending_uploads() -> Dict[str, Optional[dict]]:
    return {key: pending_upload(key) for key in EXTRACTION_PROFILES}


def discard_pending_upload(category: Optional[str] = "A") -> bool:
    key = resolve_profile(category)["key"]
    upload_path, catalog_path = _pending_paths(key)
    with _PENDING_LOCK:
        existed = upload_path.exists() or catalog_path.exists()
        _clear_pending_files(key)
        had_state = key in _pending_states
        _pending_states.pop(key, None)
        return existed or had_state


def stage_pending_upload(source: Path, category: Optional[str] = "A") -> dict:
    key = resolve_profile(category)["key"]
    new_catalog = build_price_catalog(source, category=key)
    old_catalog = get_inventory_catalog(key)
    diff = build_inventory_diff(old_catalog, new_catalog)
    with _PENDING_LOCK:
        destination, _ = _pending_paths(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
        _save_pending_catalog(new_catalog, key)
        _pending_states[key] = _pending_payload(new_catalog, diff, key)
        return dict(_pending_states[key])


def apply_pending_upload(keep_skus: List[str], skip_skus: List[str], category: Optional[str] = "A") -> dict:
    key = resolve_profile(category)["key"]
    upload_path, _ = _pending_paths(key)
    with _PENDING_LOCK:
        if key not in _pending_states or not upload_path.exists():
            raise FileNotFoundError("没有待确认的库存上传")
        new_catalog = _load_pending_catalog(key)
        if new_catalog is None:
            new_catalog = build_price_catalog(upload_path, category=key)
        old_catalog = get_inventory_catalog(key)
        merged: Dict[str, dict] = {}
        keep, skip = set(keep_skus), set(skip_skus)
        for sku, item in old_catalog.items():
            if sku in new_catalog:
                if sku in keep:
                    merged[sku] = old_catalog[sku]
                else:
                    merged[sku] = new_catalog[sku]
            elif sku in skip:
                merged[sku] = old_catalog[sku]
        for sku, item in new_catalog.items():
            if sku not in merged and sku not in skip:
                merged[sku] = item
        inventory_path = inventory_path_for(key)
        os.replace(upload_path, inventory_path)
        state, _pending_states[key] = _pending_states[key], None
        _pending_states.pop(key, None)
        invalidate_cache(key)
        save_inventory_catalog(inventory_signature(inventory_path), merged, key)
        return {"merged": merged, "state": state}


def filter_catalog_items(
    catalog: Dict[str, dict],
    *,
    query: str = "",
    set_type: str = "",
    source_sheet: str = "",
    price_min: Optional[float] = None,
    price_max: Optional[float] = None,
    row_min: Optional[int] = None,
    row_max: Optional[int] = None,
) -> list:
    """多条件筛选库存明细。

    所有条件为 AND 关系，未提供的条件不参与过滤。
    - query：一个输入框同时匹配 SKU 与类型，命中其一即算匹配
    - set_type / source_sheet：大小写不敏感的包含匹配
    - 价格与行号：区间匹配，缺值记录在启用该区间时被排除
    """
    keyword = (query or "").strip().upper()
    type_keyword = (set_type or "").strip().upper()
    sheet_keyword = (source_sheet or "").strip().upper()

    def matches(sku: str, item: dict) -> bool:
        item_type = str(item.get("set_type") or "")
        if keyword and keyword not in sku.upper() and keyword not in item_type.upper():
            return False
        if type_keyword and type_keyword not in item_type.upper():
            return False
        if sheet_keyword and sheet_keyword not in str(item.get("source_sheet") or "").upper():
            return False
        if price_min is not None or price_max is not None:
            price = item.get("price")
            if price is None:
                return False
            if price_min is not None and price < price_min:
                return False
            if price_max is not None and price > price_max:
                return False
        if row_min is not None or row_max is not None:
            row = item.get("source_row")
            if row is None:
                return False
            if row_min is not None and row < row_min:
                return False
            if row_max is not None and row > row_max:
                return False
        return True

    return [item for sku, item in sorted(catalog.items()) if matches(sku, item)]
