"""模块权限：一级模块 + 店铺管理下的二级模块。

权限存在 `users.modules` / `users.store_modules`（JSON 数组文本）。
`store_access` 保留为兼容别名——它等价于 `"store" in modules`，老代码继续可用。
"""

from __future__ import annotations

from typing import Optional

from app.database import _dump_json_list, _load_json_list

# 一级模块。key 与前端菜单/路由保持一致。
MODULES = [
    {"key": "orders", "label": "订单计算", "path": "/orders"},
    {"key": "inventory", "label": "库存管理", "path": "/inventory"},
    {"key": "activities", "label": "批量报名活动", "path": "/activities"},
    {"key": "tasks", "label": "任务记录", "path": "/tasks"},
    {"key": "store", "label": "店铺管理", "path": "/store"},
]

# 店铺管理下的二级模块。
STORE_MODULES = [
    {"key": "goods", "label": "商品查询", "path": "/store/goods"},
    {"key": "create", "label": "创建商品", "path": "/store/create"},
    {"key": "kefu", "label": "客服自助", "path": "/store/kefu"},
    {"key": "actions", "label": "操作记录", "path": "/store/actions"},
]

MODULE_KEYS = [m["key"] for m in MODULES]
STORE_MODULE_KEYS = [m["key"] for m in STORE_MODULES]

# 默认：普通用户有除店铺以外的全部模块；管理员全有
DEFAULT_USER_MODULES = [k for k in MODULE_KEYS if k != "store"]


def normalize_modules(value, *, is_admin: bool = False) -> list[str]:
    """过滤非法 key 并去重；管理员恒为全量。"""
    if is_admin:
        return list(MODULE_KEYS)
    items = [str(x) for x in (value or []) if str(x) in MODULE_KEYS]
    # 保持定义顺序，避免前端勾选顺序影响展示
    return [k for k in MODULE_KEYS if k in items]


def normalize_store_modules(value) -> list[str]:
    items = [str(x) for x in (value or []) if str(x) in STORE_MODULE_KEYS]
    return [k for k in STORE_MODULE_KEYS if k in items]


def dump_modules(modules) -> str:
    return _dump_json_list(normalize_modules(modules))


def dump_store_modules(modules) -> str:
    return _dump_json_list(normalize_store_modules(modules))


def modules_of(user: dict) -> list[str]:
    """从 user dict 读一级模块；管理员恒全量。

    `store_access` 为真而 modules 里没有 store 的老数据，视为有 store。
    """
    if (user.get("role") or "") == "admin":
        return list(MODULE_KEYS)
    raw = user.get("modules")
    mods = normalize_modules(raw if isinstance(raw, list) else _load_json_list(raw))
    if not mods:
        # 没配置过权限的老用户/新用户：按历史行为给默认值（除店铺外全开），
        # 否则加了中间件后所有人都会被 403 挡住
        mods = list(DEFAULT_USER_MODULES)
        if user.get("store_access"):
            mods = [k for k in MODULE_KEYS if k in (set(mods) | {"store"})]
        return mods
    if user.get("store_access") and "store" not in mods:
        mods = [k for k in MODULE_KEYS if k in (set(mods) | {"store"})]
    return mods


def store_modules_of(user: dict) -> list[str]:
    """店铺二级模块；有 store 权限但没配二级时，默认全开（兼容老用户）。"""
    if (user.get("role") or "") == "admin":
        return list(STORE_MODULE_KEYS)
    raw = user.get("store_modules")
    mods = normalize_store_modules(raw if isinstance(raw, list) else _load_json_list(raw))
    if not mods and "store" in modules_of(user):
        return list(STORE_MODULE_KEYS)
    return mods


def has_module(user: dict, key: str) -> bool:
    return key in modules_of(user)


def has_store_module(user: dict, key: str) -> bool:
    return key in store_modules_of(user)


def label_of(key: str) -> str:
    """模块 key → 中文名（错误提示里给用户看）。"""
    for m in MODULES:
        if m["key"] == key:
            return m["label"]
    for m in STORE_MODULES:
        if m["key"] == key:
            return m["label"]
    return key


def catalog() -> dict:
    return {"modules": MODULES, "store_modules": STORE_MODULES}


def apply_to_form(user: dict) -> dict:
    """给前端 /api/me 用的权限快照。"""
    return {
        "modules": modules_of(user),
        "store_modules": store_modules_of(user),
        "is_admin": (user.get("role") or "") == "admin",
    }
