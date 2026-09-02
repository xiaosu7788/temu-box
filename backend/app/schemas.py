from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class SkuQueryRequest(BaseModel):
    skus: List[str] = Field(default_factory=list, max_length=500)


class InventoryItemCreateRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=255)
    price: Optional[float] = Field(default=None, ge=0, le=1000000)
    set_type: str = Field(default="单品", min_length=1, max_length=64)


class InventoryItemUpdateRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=255)
    price: Optional[float] = Field(default=None, ge=0, le=1000000)
    set_type: str = Field(default="单品", min_length=1, max_length=64)

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


class ActivitySkuRulesPayload(BaseModel):
    set_keywords: List[str] = Field(default_factory=list)
    set_mappings: List[ActivitySetMappingPayload] = Field(default_factory=list)
    single_mode: str
    single_delimiter: str = "-"
    single_marker: str = "price"


class SettingsPayload(BaseModel):
    order: Dict[str, object]
    activity: Dict[str, object]

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
