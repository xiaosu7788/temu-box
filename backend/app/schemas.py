from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class SkuQueryRequest(BaseModel):
    skus: List[str] = Field(default_factory=list, max_length=500)
    inventory_category: Optional[str] = Field(default=None, max_length=16)


class InventoryItemCreateRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=255)
    price: Optional[float] = Field(default=None, ge=0, le=1000000)
    set_type: str = Field(default="单品", min_length=1, max_length=64)
    inventory_category: Optional[str] = Field(default=None, max_length=16)
    manual_combo: bool = False


class InventoryItemUpdateRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=255)
    price: Optional[float] = Field(default=None, ge=0, le=1000000)
    set_type: str = Field(default="单品", min_length=1, max_length=64)
    inventory_category: Optional[str] = Field(default=None, max_length=16)
    manual_combo: Optional[bool] = None
class InventoryApplyRequest(BaseModel):
    keep_skus: List[str] = Field(default_factory=list)
    skip_skus: List[str] = Field(default_factory=list)
    inventory_category: Optional[str] = Field(default=None, max_length=16)

class InventoryCategoryCreateRequest(BaseModel):
    key: str = Field(min_length=1, max_length=16)
    label: str = Field(min_length=1, max_length=80)
    code_pattern: Optional[str] = Field(default=None, max_length=255)
    price_max: float = Field(default=100, gt=0, le=1000000)


class InventoryCategoryUpdateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    code_pattern: Optional[str] = Field(default=None, max_length=255)
    price_max: float = Field(gt=0, le=1000000)
    enabled: bool = True
    sort_order: int = Field(default=100, ge=-10000, le=10000)

class SkuResult(BaseModel):
    sku: str
    found: bool
    price: Optional[float] = None
    set_type: Optional[str] = None
    source_sheet: Optional[str] = None
    source_row: Optional[int] = None
    source_column: Optional[int] = None


class TaskSummary(BaseModel):
    id: str
    status: str
    progress: int
    message: str
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    stats: Dict[str, object] = Field(default_factory=dict)
    logs: List[str] = Field(default_factory=list)
    download_ready: bool = False


class HalfHeadcostEntry(BaseModel):
    sku: str
    set_type: str


class HalfHeadcostCreateRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=255)
    set_type: str = Field(default="单品", min_length=1, max_length=64)
    category_code: Optional[str] = Field(default=None, max_length=16)
    inventory_category: Optional[str] = Field(default=None, max_length=16)


class HalfHeadcostUpdateRequest(BaseModel):
    set_type: str = Field(default="单品", min_length=1, max_length=64)
    category_code: Optional[str] = Field(default=None, max_length=16)
    inventory_category: Optional[str] = Field(default=None, max_length=16)


class WeightHeadcostCreateRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=255)
    weight_g: float = Field(gt=0, le=1000000)
    category_code: Optional[str] = Field(default=None, max_length=16)
    inventory_category: Optional[str] = Field(default=None, max_length=16)


class WeightHeadcostUpdateRequest(BaseModel):
    weight_g: float = Field(gt=0, le=1000000)
    category_code: Optional[str] = Field(default=None, max_length=16)
    inventory_category: Optional[str] = Field(default=None, max_length=16)

class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=120)


class LoginRequest(BaseModel):
    username: str
    password: str


class AdminUserUpdateRequest(BaseModel):
    username: Optional[str] = Field(default=None, min_length=3, max_length=80)
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)
    display_name: Optional[str] = Field(default=None, max_length=120)
    note: Optional[str] = Field(default=None, max_length=500)
    store_access: Optional[bool] = None
    modules: Optional[list[str]] = None
    store_modules: Optional[list[str]] = None



class AdminUserBulkUpdateRequest(BaseModel):
    user_ids: List[int] = Field(min_length=1, max_length=500)
    display_name: Optional[str] = Field(default=None, max_length=120)
    note: Optional[str] = Field(default=None, max_length=500)
    status: Optional[str] = Field(default=None, max_length=20)
    store_access: Optional[bool] = None


class StoreLoginRequest(BaseModel):
    """登录自己的 Temu 店铺。

    use_remembered=True 时服务端直接用「记住密码」里的凭据，密码不需要经过前端。
    """

    account: Optional[str] = Field(default=None, max_length=160)
    password: Optional[str] = Field(default=None, max_length=128)
    mall_id: Optional[str] = Field(default=None, max_length=64)
    remember: bool = False
    use_remembered: bool = False
    # 触发风控（54001）时用户收到的验证码
    verify_code: Optional[str] = Field(default=None, max_length=16)
    # 用「店铺账号库」里这一条登录（切换店铺时前端只传 id，密码不经过浏览器）
    account_id: Optional[int] = None


class StoreKefuQueryRequest(BaseModel):
    """客服工具的只读查询（提交前确认商品身份）。"""

    type: str = Field(min_length=2, max_length=8)
    id: str = Field(min_length=1, max_length=64)
    tool_id: Optional[str] = Field(default=None, max_length=32)


class StoreKefuActionRequest(BaseModel):
    """创建一次客服写操作预览（下架 / 发布 / 刷新 / 异常查询）。

    创建时会一次性完成「查商品 + 拦截检查 + 组装报文」并落库；
    真正发送要再调 `POST /api/store/kefu/actions/{id}/confirm`。
    """

    tool_id: Optional[str] = Field(default=None, max_length=32)
    tool_name: Optional[str] = Field(default=None, max_length=80)
    data_type: Optional[str] = Field(default=None, max_length=8)
    data_id: str = Field(min_length=1, max_length=64)
    reason_code: Optional[int] = None
    reason_detail: Optional[str] = Field(default=None, max_length=200)
    parent_msg_id: Optional[str] = Field(default=None, max_length=32)


class StoreKefuQueryBatchRequest(BaseModel):
    """批量只读查询：一串编号换商品详情（只读，可反复执行）。"""

    type: str = Field(min_length=2, max_length=8)
    ids: str = Field(min_length=1, max_length=8000)
    tool_id: Optional[str] = Field(default=None, max_length=32)


class StoreKefuBatchRequest(BaseModel):
    """批量执行客服工具（多行/逗号分隔的编号，最多 50 个）。"""

    tool_id: Optional[str] = Field(default=None, max_length=32)
    tool_name: Optional[str] = Field(default=None, max_length=80)
    data_type: Optional[str] = Field(default=None, max_length=8)
    ids: str = Field(min_length=1, max_length=8000)
    reason_code: Optional[int] = None
    reason_detail: Optional[str] = Field(default=None, max_length=200)


class AdminUserBulkDeleteRequest(BaseModel):
    user_ids: List[int] = Field(min_length=1, max_length=500)



class ActivitySetMappingPayload(BaseModel):
    pattern: str
    pieces: int


class ActivitySingleRulePayload(BaseModel):
    mode: str
    delimiter: str = ""
    marker: str = ""


class ActivitySkuRulesPayload(BaseModel):
    set_keywords: List[str] = Field(default_factory=list)
    set_mappings: List[ActivitySetMappingPayload] = Field(default_factory=list)
    single_rules: Optional[List[ActivitySingleRulePayload]] = None
    # 兼容旧前端：单条规则的旧字段
    single_mode: Optional[str] = None
    single_delimiter: str = "-"
    single_marker: str = "price"


class SettingsPayload(BaseModel):
    order: Dict[str, object]
    activity: Dict[str, object]


class SystemSettingsPayload(BaseModel):
    """系统设置。字段必须覆盖界面全部项，否则 Pydantic 会丢弃多余字段，
    导致这些设置（尤其店铺相关的 4 项）无论怎么改都存不进去。"""

    task_workers: int = Field(ge=1, le=16)
    task_queue_limit: int = Field(ge=1, le=1000)
    activity_workers: int = Field(ge=1, le=16)
    activity_queue_limit: int = Field(ge=1, le=1000)
    cleanup_enabled: bool = True
    cleanup_retention_days: int = Field(ge=1, le=3650)
    audit_retention_days: int = Field(ge=7, le=3650)
    # 店铺模块
    kefu_writes_enabled: bool = True
    kefu_auto_retry_enabled: bool = True
    store_login_log_retention_days: int = Field(ge=7, le=3650)
    kefu_action_retention_days: int = Field(ge=7, le=3650)

class RegionCreateRequest(BaseModel):
    code: str = Field(min_length=2, max_length=16)
    name: str = Field(min_length=1, max_length=80)
    currency: str = Field(default="CNY", min_length=3, max_length=3)
    copy_from: Optional[str] = Field(default=None, max_length=16)
    sort_order: int = Field(default=100, ge=-10000, le=10000)


class RegionUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    currency: str = Field(default="CNY", min_length=3, max_length=3)
    enabled: bool = True
    is_default: bool = False
    sort_order: int = Field(default=100, ge=-10000, le=10000)
    order_strategy: str = "standard_order_v1"
    activity_strategy: str = "standard_activity_v1"
    settings: SettingsPayload


class CategoryCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=16)
    name: str = Field(min_length=1, max_length=80)
    template_type: str = Field(default="set_based", min_length=1, max_length=20)
    set_types: List[int] = Field(default_factory=list, max_length=12)
    allowed_regions: Optional[List[str]] = None
    inventory_category: Optional[str] = Field(default=None, max_length=16)
    sort_order: int = Field(default=100, ge=-10000, le=10000)


class CategoryUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    template_type: str = Field(min_length=1, max_length=20)
    set_types: List[int] = Field(default_factory=list, max_length=12)
    allowed_regions: Optional[List[str]] = None
    enabled: bool = True
    is_default: bool = False
    inventory_category: Optional[str] = Field(default=None, max_length=16)
    sort_order: int = Field(default=100, ge=-10000, le=10000)
