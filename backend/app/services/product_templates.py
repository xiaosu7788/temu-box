"""手动创建商品的表单模板：存取一批填好的属性，下次复用。

复用时刻意跳过易变字段（标题、图片、货号、颜色规格）——那些每个商品都不一样。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, or_, select

from app.database import ProductFormTemplate, db_session
from app.services.store_session import StoreError, account_hash

# 保存时完整存，回填时只取这些「与商品无关、可复用」的字段
REUSABLE_KEYS = (
    "catPath",               # 必须复用：selected 的属性 key 依赖类目模板
    "siteId",                # 经营站点（不同站点的仓库/尺码组不同）
    "warehouseIds",          # 发货仓
    "selected",              # 类目属性（材质/风格/护理说明…）—— 核心
    "origin",                # 产地国家 + 省份
    "manufacturing",         # 制造地点
    "volume",
    "weight",
    "isSensitive",
    "sensitiveTypes",
    "sensitiveList",
    "skuClassification",
    "pieces",
    "individuallyPacked",
    "netContent",
    "totalNetContent",
    "shipmentLimit",
    "freightTemplateId",
    "materialMultiLanguages",
    "referenceLink",
    "suggestedRetailPrice",
    "skuPrice",
    "stock",
    "sizeVids",              # 尺码勾选（用户只说要换颜色规格）
    # 前端 buildForm 存的字段名是 sizeTemplateSourceId（蓝本 id，SDK 会据此建新表）；
    # 只写 sizeTemplateId 会漏掉，编辑模板时下拉永远是空的
    "sizeTemplateSourceId",
    "sizeTemplateId",
    "generalSizeType",
)
# 明确不复用（写在这里是为了让后来人一眼看到边界）
VOLATILE_KEYS = ("name", "enName", "images", "materialImgUrl", "decorationImages", "extCode", "colorVid")

MAX_TEMPLATES = 50
MAX_NAME = 60
MAX_NOTE = 300


def _shop_filter(stmt, shop_hash: str):
    """只返回当前店铺的模板；account_hash 为空的是老数据，对所有店铺可见。"""
    if not shop_hash:
        return stmt
    return stmt.where(
        or_(
            ProductFormTemplate.account_hash == "",
            ProductFormTemplate.account_hash == shop_hash,
        )
    )


def list_templates(user_id: int, shop: str = "") -> dict:
    """shop 传当前绑定的 Temu 账号；传空表示不按店铺过滤（后台管理用）。"""
    shop_hash = account_hash(shop) if shop else ""
    with db_session() as session:
        rows = session.scalars(
            _shop_filter(
                select(ProductFormTemplate).where(ProductFormTemplate.user_id == user_id),
                shop_hash,
            ).order_by(ProductFormTemplate.updated_at.desc())
        ).all()
    return {
        "total": len(rows),
        "items": [
            {
                "id": r.id,
                "name": r.name,
                "note": r.note,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                "field_count": len((json.loads(r.form_json or "{}") or {}).get("selected") or {}),
            }
            for r in rows
        ],
    }


def get_template(user_id: int, template_id: int, shop: str = "") -> dict:
    shop_hash = account_hash(shop) if shop else ""
    with db_session() as session:
        row = session.get(ProductFormTemplate, template_id)
        if not row or row.user_id != user_id:
            raise StoreError("模板不存在", code="NOT_FOUND", status=404)
        # 模板属于别的店铺时也当作不存在，避免串用参数
        if shop_hash and row.account_hash and row.account_hash != shop_hash:
            raise StoreError("该模板属于另一个店铺，切换回对应店铺后再使用", code="NOT_FOUND", status=404)
        raw = json.loads(row.form_json or "{}") or {}
        # 只回填可复用字段，前端就不用自己挑
        values = {k: raw[k] for k in REUSABLE_KEYS if k in raw}
        return {"id": row.id, "name": row.name, "note": row.note, "values": values}


def save_template(user_id: int, name: str, note: str, form: dict, shop: str = "") -> dict:
    name = (name or "").strip()
    note = (note or "").strip()
    if not name:
        raise StoreError("请填写模板名称", code="ARGS", status=400)
    if len(name) > MAX_NAME:
        raise StoreError(f"模板名称最多 {MAX_NAME} 字", code="ARGS", status=400)
    if len(note) > MAX_NOTE:
        raise StoreError(f"备注最多 {MAX_NOTE} 字", code="ARGS", status=400)
    if not isinstance(form, dict) or not form:
        raise StoreError("表单内容为空", code="ARGS", status=400)

    with db_session() as session:
        mine = session.scalars(
            select(ProductFormTemplate).where(ProductFormTemplate.user_id == user_id)
        ).all()
        existing = next((r for r in mine if r.name == name), None)
        if not existing and len(mine) >= MAX_TEMPLATES:
            raise StoreError(f"模板数量已达上限 {MAX_TEMPLATES}，请先删除一些", code="ARGS", status=400)
        # 只存可复用字段：标题、颜色、图片、货号这些本来就不该进模板
        slim = {k: v for k, v in form.items() if k in REUSABLE_KEYS}
        if not slim.get("selected"):
            raise StoreError("表单里没有可保存的属性，请先选择类目并填写属性", code="ARGS", status=400)
        payload = json.dumps(slim, ensure_ascii=False)
        if existing:
            existing.note = note
            existing.form_json = payload
            existing.updated_at = datetime.now(timezone.utc)
            return {"id": existing.id, "name": existing.name, "updated": True}
        row = ProductFormTemplate(
            user_id=user_id,
            name=name,
            note=note,
            form_json=payload,
            account_hash=account_hash(shop) if shop else "",
        )
        session.add(row)
        session.flush()
        return {"id": row.id, "name": row.name, "updated": False}


def update_template(
    user_id: int,
    template_id: int,
    *,
    name: Optional[str] = None,
    note: Optional[str] = None,
    form: Optional[dict] = None,
) -> dict:
    """改名称 / 备注；传 form 时用新参数覆盖模板内容。"""
    with db_session() as session:
        row = session.get(ProductFormTemplate, template_id)
        if not row or row.user_id != user_id:
            raise StoreError("模板不存在", code="NOT_FOUND", status=404)

        if name is not None:
            clean = name.strip()
            if not clean:
                raise StoreError("模板名称不能为空", code="ARGS", status=400)
            if len(clean) > MAX_NAME:
                raise StoreError(f"模板名称最多 {MAX_NAME} 字", code="ARGS", status=400)
            # 同名去重：改名撞车时报错，别静默合并
            dup = session.scalars(
                select(ProductFormTemplate).where(
                    ProductFormTemplate.user_id == user_id,
                    ProductFormTemplate.name == clean,
                    ProductFormTemplate.id != template_id,
                )
            ).first()
            if dup:
                raise StoreError(f"已有同名模板「{clean}」", code="ARGS", status=400)
            row.name = clean
        if note is not None:
            if len(note.strip()) > MAX_NOTE:
                raise StoreError(f"备注最多 {MAX_NOTE} 字", code="ARGS", status=400)
            row.note = note.strip()
        if form:
            slim = {k: v for k, v in form.items() if k in REUSABLE_KEYS}
            if not slim.get("selected"):
                raise StoreError("表单里没有可保存的属性", code="ARGS", status=400)
            row.form_json = json.dumps(slim, ensure_ascii=False)
        row.updated_at = datetime.now(timezone.utc)
        return {"id": row.id, "name": row.name, "note": row.note, "form_updated": bool(form)}


def delete_template(user_id: int, template_id: int) -> dict:
    with db_session() as session:
        row = session.get(ProductFormTemplate, template_id)
        if not row or row.user_id != user_id:
            raise StoreError("模板不存在", code="NOT_FOUND", status=404)
        session.execute(delete(ProductFormTemplate).where(ProductFormTemplate.id == template_id))
    return {"ok": True}

def admin_user_overview() -> dict:
    """后台「用户管理」：开通店铺的用户 + 建品统计 + 模板数 + 店铺会话状态。"""
    from app.database import (
        list_users,
        product_create_stats,
        list_temu_sessions,
    )

    users = {u["id"]: u for u in list_users()}
    sessions = {s["user_id"]: s for s in list_temu_sessions()}
    # 每个用户的模板数
    with db_session() as session:
        rows = session.scalars(select(ProductFormTemplate)).all()
    tpl_count: dict[int, int] = {}
    for r in rows:
        tpl_count[r.user_id] = tpl_count.get(r.user_id, 0) + 1

    items = []
    for uid, u in users.items():
        is_admin_user = (u.get("role") or "") == "admin"
        # 只列「有店铺权限」的用户；管理员恒有权限（与 store_user 依赖一致）
        if not (bool(u.get("store_access")) or is_admin_user):
            continue
        s = sessions.get(uid) or {}
        items.append({
            "user_id": uid,
            "username": u.get("username") or "",
            "display_name": u.get("display_name") or "",
            # list_users() 返回的是 role（admin / user），没有 is_admin 字段
            "is_admin": (u.get("role") or "") == "admin",
            # 管理员恒有店铺权限（与 store_user 依赖的判定保持一致），
            # 数据库里可能还是 False，否则界面会显示「未开通」但会话却是在线
            "store_access": bool(u.get("store_access")) or (u.get("role") or "") == "admin",
            "template_count": tpl_count.get(uid, 0),
            "stats": product_create_stats(uid),
            "store": {
                "account_masked": s.get("account_masked") or "",
                "status": s.get("status") or "offline",
                "mall_name": s.get("mall_name") or "",
                "login_at": s.get("login_at"),
                "expires_at": s.get("expires_at"),
                "last_seen_at": s.get("last_seen_at"),
                "remember": bool(s.get("remember")),
                "last_error": s.get("last_error") or "",
            },
        })
    items.sort(key=lambda x: (-x["stats"]["today_ok"], -x["stats"]["total_ok"]))
    return {"items": items, "total": len(items)}


def admin_user_templates(user_id: int) -> dict:
    """某用户保存的表单模板。"""
    with db_session() as session:
        rows = session.scalars(
            select(ProductFormTemplate)
            .where(ProductFormTemplate.user_id == user_id)
            .order_by(ProductFormTemplate.updated_at.desc())
        ).all()
    return {
        "total": len(rows),
        "items": [
            {
                "id": r.id,
                "name": r.name,
                "note": r.note,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                "field_count": len((json.loads(r.form_json or "{}") or {}).get("selected") or {}),
            }
            for r in rows
        ],
    }
