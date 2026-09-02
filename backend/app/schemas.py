from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class SkuQueryRequest(BaseModel):
    skus: List[str] = Field(default_factory=list, max_length=500)


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
