from __future__ import annotations

import io
import random
import re
from pathlib import Path
from typing import Dict, Optional, Tuple

from openpyxl import load_workbook


ACTIVITY_PRICE_BASE = {
    4: 42.0,
    5: 45.0,
    6: 48.0,
    8: 71.0,
    10: 75.0,
    12: 92.0,
}
SINGLE_SKC_RE = re.compile(r"-([0-9]+(?:\.[0-9]+)?)\s*$")
SET_SKC_RE = re.compile(r"^y\d+\s*-\s*(\d+)\s*piece\s*$", re.IGNORECASE)
HEADER_NAMES = {
    "skc": "SKC货号",
    "price": "活动申报价格",
}


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _find_headers(workbook) -> Tuple[object, int, Dict[str, int]]:
    for worksheet in workbook.worksheets:
        for row in range(1, min(worksheet.max_row, 30) + 1):
            columns = {}
            for column in range(1, worksheet.max_column + 1):
                value = _text(worksheet.cell(row, column).value)
                for key, header in HEADER_NAMES.items():
                    if value == header:
                        columns[key] = column
            if set(columns) == set(HEADER_NAMES):
                return worksheet, row, columns
    raise ValueError("未找到同时包含“SKC货号”和“活动申报价格”的工作表")


def parse_skc(skc: object) -> Optional[Tuple[str, float]]:
    value = _text(skc)
    if not value:
        return None

    set_match = SET_SKC_RE.fullmatch(value)
    if set_match:
        pieces = int(set_match.group(1))
        if pieces not in ACTIVITY_PRICE_BASE:
            return None
        return "set", float(pieces)

    single_match = SINGLE_SKC_RE.search(value)
    if single_match:
        return "single", float(single_match.group(1))
    return None


def activity_base_price(parsed: Tuple[str, float], settings=None) -> float:
    kind, value = parsed
    activity_settings = (settings or {}).get("activity", {})
    if kind == "set":
        set_prices = activity_settings.get("set_prices", {})
        return float(set_prices.get(str(int(value)), ACTIVITY_PRICE_BASE[int(value)]))
    tiers = activity_settings.get("single_tiers", [{"min_price": 0, "profit": 0}])
    profit = max((float(tier.get("profit", 0)) for tier in tiers if value >= float(tier.get("min_price", 0))), default=0)
    return value + float(activity_settings.get("headcost", 5)) + float(activity_settings.get("operation_fee", 7)) + profit


def _reference_price(value: object) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if price >= 0 else None


def _uplifted_price(base: float, reference: float) -> float:
    max_cents = min(100, int(round((reference - base) * 100)))
    if max_cents <= 0:
        return round(base, 2)
    uplift_cents = random.randint(1, max_cents)
    return round(base + uplift_cents / 100, 2)


def process_activity_workbook(source: bytes, output_path: Path, settings=None) -> dict:
    if not source:
        raise ValueError("上传的报名表为空")

    workbook = load_workbook(io.BytesIO(source), data_only=False, keep_links=False)
    try:
        worksheet, header_row, columns = _find_headers(workbook)
        price_column = columns["price"]
        skc_column = columns["skc"]
        rows_to_delete = []
        updated = 0
        unchanged = 0
        skipped = 0
        processed = 0
        input_data_rows = max(0, worksheet.max_row - header_row)

        for row in range(header_row + 1, worksheet.max_row + 1):
            skc = worksheet.cell(row, skc_column).value
            if not _text(skc):
                skipped += 1
                continue
            parsed = parse_skc(skc)
            reference = _reference_price(worksheet.cell(row, price_column).value)
            if parsed is None or reference is None:
                skipped += 1
                continue

            processed += 1
            base = activity_base_price(parsed, settings)
            if reference < base:
                rows_to_delete.append(row)
                continue
            if abs(reference - base) < 0.000001:
                unchanged += 1
                continue

            worksheet.cell(row, price_column).value = _uplifted_price(base, reference)
            worksheet.cell(row, price_column).number_format = "0.00"
            updated += 1

        for row in reversed(rows_to_delete):
            worksheet.delete_rows(row, 1)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        workbook.save(output_path)
        return {
            "sheet": worksheet.title,
            "header_row": header_row,
            "input_data_rows": input_data_rows,
            "processed_rows": processed,
            "updated_rows": updated,
            "unchanged_rows": unchanged,
            "removed_rows": len(rows_to_delete),
            "skipped_rows": skipped,
            "remaining_data_rows": input_data_rows - len(rows_to_delete),
        }
    finally:
        workbook.close()
