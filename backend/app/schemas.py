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
