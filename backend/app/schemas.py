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


class InventoryItemUpdateRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=255)
    price: Optional[float] = Field(default=None, ge=0, le=1000000)
    set_type: str = Field(default="单品", min_length=1, max_length=64)
    inventory_category: Optional[str] = Field(default=None, max_length=16)
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

class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=120)


class LoginRequest(BaseModel):
    username: str
    password: str


class AdminUserUpdateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)


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
    task_workers: int = Field(ge=1, le=16)
    task_queue_limit: int = Field(ge=1, le=1000)
    activity_workers: int = Field(ge=1, le=16)
    activity_queue_limit: int = Field(ge=1, le=1000)
    cleanup_enabled: bool = True
    cleanup_retention_days: int = Field(ge=1, le=3650)
    audit_retention_days: int = Field(ge=7, le=3650)

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
