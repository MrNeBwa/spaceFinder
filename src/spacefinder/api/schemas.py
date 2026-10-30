"""Pydantic-схемы запросов и ответов."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..core.models import (
    DeleteMode,
    DeleteStatus,
    RiskLevel,
    RuleCategory,
    ScanStatus,
)


class RuleOut(BaseModel):
    id: str
    title: str
    description: str
    risk: RiskLevel
    category: RuleCategory
    default_selected: bool
    requires_privilege: bool
    commands: list[str] = Field(default_factory=list)


class ConfigOut(BaseModel):
    dry_run: bool
    allow_permanent: bool
    version: str
    home: str


class DiskOut(BaseModel):
    total: int
    used: int
    free: int
    percent: float


class ProgressOut(BaseModel):
    phase: str
    items_found: int
    bytes_found: int
    current_path: str
    truncated: bool


class TotalsOut(BaseModel):
    all_bytes: int
    default_selected_bytes: int
    items: int


class ItemOut(BaseModel):
    id: str
    rule_id: str
    rule_title: str
    path: str
    kind: Literal["file", "directory"]
    risk: RiskLevel
    size_bytes: int
    apparent_bytes: int
    mtime: float
    default_selected: bool
    warnings: list[str]
    deleted: bool
    in_trash: bool


class ScanOut(BaseModel):
    scan_id: str
    status: ScanStatus
    created_at: float
    finished_at: float | None
    progress: ProgressOut
    totals: TotalsOut
    items: list[ItemOut]
    warnings: list[str]


class CreateScanRequest(BaseModel):
    rule_ids: list[str] | None = None
    # Флаг оставлен для совместимости с клиентами: системные кэши всё
    # равно только показываются, но не сканируются и не удаляются.
    include_system: bool = False
    model_config = ConfigDict(extra="forbid")


class DeleteRequest(BaseModel):
    # Верхняя граница совпадает с максимальным SPACEFINDER_MAX_ITEMS,
    # иначе «выбрать всё» на большом результате неожиданно получало бы 400.
    item_ids: list[str] = Field(min_length=1, max_length=100_000)
    mode: DeleteMode = DeleteMode.TRASH
    confirm: bool = False
    model_config = ConfigDict(extra="forbid")


class DeleteResultOut(BaseModel):
    item_id: str
    status: DeleteStatus
    freed_bytes: int
    target: str | None = None
    error_code: str | None = None
    message: str | None = None


class DeleteResponse(BaseModel):
    results: list[DeleteResultOut]
    freed_bytes: int


class HistoryResponse(BaseModel):
    entries: list[dict[str, Any]]
