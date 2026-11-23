"""一次性数据修正：把历史上被误判的回执阶段改回正确值。

背景：早期版本把平台固定话术（「已进入加速等待序列」）当成执行结果写成了 `replied`，
导致列表里显示「已完成」但其实还在排队；失败话术（「系统忙碌中，请您稍后重试」）
也曾被当成 `accepted`。

用法（在 backend/ 下执行）：

    .\\.venv\\Scripts\\python.exe scripts\\fix_kefu_stage.py            # 只预览，不改库
    .\\.venv\\Scripts\\python.exe scripts\\fix_kefu_stage.py --apply    # 真正写库

建议先备份 data/temubox.db。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.database import TemuKefuAction  # noqa: E402
from app.services.store_kefu_actions import (  # noqa: E402
    FAILED_REPLY_HINTS,
    MAX_RETRY,
    REJECTED_REPLY_HINTS,
    classify_reply,
)

TARGET_STAGES = ("replied", "accepted", "sent", "unknown")


def _reply_of(row: TemuKefuAction) -> dict:
    """robot_reply 在库里是 JSON 字符串，解析成 {contentType, content, msgId}。"""
    raw = row.robot_reply or ""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {"content": str(raw)}
    return parsed if isinstance(parsed, dict) else {"content": str(parsed)}


def _classify_row(row: TemuKefuAction) -> tuple[str, dict]:
    reply = _reply_of(row)
    return classify_reply(reply), reply


def main() -> int:
    parser = argparse.ArgumentParser(description="修正客服回执阶段的历史误判")
    parser.add_argument("--apply", action="store_true", help="真正写库（默认只预览）")
    args = parser.parse_args()

    changed = 0
    with SessionLocal() as db:
        rows = (
            db.query(TemuKefuAction)
            .filter(TemuKefuAction.stage.in_(TARGET_STAGES))
            .order_by(TemuKefuAction.created_at.desc())
            .all()
        )
        for row in rows:
            reply = _reply_of(row)
            if not reply:
                continue
            text = str(reply.get("content") or "")
            stage, _ = _classify_row(row)
            if stage in ("replied", "sent"):
                continue

            new_stage = stage
            error = row.error or ""
            if new_stage == "failed":
                retry_count = row.retry_count or 0
                if retry_count < MAX_RETRY:
                    new_stage = "retry_pending"
                detail = next((hint for hint in FAILED_REPLY_HINTS if hint in text), "失败")
                error = f"{detail}：{text[:180]}"
            elif new_stage == "rejected":
                detail = next((hint for hint in REJECTED_REPLY_HINTS if hint in text), "平台拒绝")
                error = f"{detail}：{text[:180]}"

            if new_stage == row.stage and error == (row.error or ""):
                continue

            changed += 1
            print(
                f"[{row.id[:8]}] {row.data_id:<16} {row.stage:<10} → {new_stage:<14} "
                f"{(text[:46]).replace(chr(10), ' ')}"
            )
            if args.apply:
                row.stage = new_stage
                row.error = error[:255]
                row.finished_at = None if new_stage in ("accepted", "retry_pending") else row.finished_at
        if args.apply:
            db.commit()

    print(f"\n共 {changed} 条待修正{'（已写入）' if args.apply else '（预览模式，加 --apply 才写库）'}")
    if not args.apply and changed:
        print("提醒：写库前请先备份 data/temubox.db")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
