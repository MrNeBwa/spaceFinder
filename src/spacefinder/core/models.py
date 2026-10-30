"""Доменные модели spaceFinder."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RuleCategory(StrEnum):
    """Как показывать правило в интерфейсе."""

    JUNK = "junk"
    APPS = "apps"
    SYSTEM = "system"


class ItemKind(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"


class ScanStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"


class DeleteMode(StrEnum):
    TRASH = "trash"
    PERMANENT = "permanent"
    DRY_RUN = "dry_run"


class DeleteStatus(StrEnum):
    TRASHED = "trashed"
    DELETED = "deleted"
    SIMULATED = "simulated"
    SKIPPED = "skipped"
    FAILED = "failed"


Selector = Literal["children", "glob", "module_dirs", "none"]


@dataclass(frozen=True, slots=True)
class ScanRule:
    """Правило: где искать мусор и насколько это рискованно."""

    id: str
    title: str
    description: str
    risk: RiskLevel
    default_selected: bool
    roots: tuple[Path, ...]
    selector: Selector = "children"
    patterns: tuple[str, ...] = ()
    min_age_days: int = 0
    # Показывать только пункты не меньше этого размера (занято на диске).
    # Для «крупных приложений» это отсекает мелочь, но не мешает удалять
    # найденное: порог применяется только к выдаче скана.
    min_size_bytes: int = 0
    category: RuleCategory = RuleCategory.JUNK
    requires_privilege: bool = False
    info_commands: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.min_size_bytes < 0:
            raise ValueError("min_size_bytes не может быть отрицательным")
        if self.selector == "module_dirs" and not self.patterns:
            raise ValueError("module_dirs требует хотя бы одно имя каталога")


@dataclass(slots=True)
class Item:
    id: str
    rule_id: str
    path: Path
    kind: ItemKind
    size_bytes: int
    apparent_bytes: int
    mtime: float
    risk: RiskLevel
    # Идентификатор объекта на момент сканирования. Перед удалением
    # сверяется повторно: если путь успели заменить, операция отклоняется.
    dev: int
    ino: int
    warnings: tuple[str, ...] = ()


@dataclass(slots=True)
class Progress:
    phase: str = "idle"
    items_found: int = 0
    bytes_found: int = 0
    current_path: str = ""
    truncated: bool = False


@dataclass(slots=True)
class ScanSession:
    id: str
    created_at: float
    status: ScanStatus
    progress: Progress
    items: dict[str, Item] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)
    # Отдельный замок на время файловых операций: session.lock остаётся
    # коротким, чтобы GET /scans не блокировался на большом удалении.
    delete_lock: threading.RLock = field(default_factory=threading.RLock)
    paths: set[str] = field(default_factory=set)
    deleted_ids: set[str] = field(default_factory=set)
    finished_at: float | None = None


@dataclass(frozen=True, slots=True)
class DeleteResult:
    item_id: str
    status: DeleteStatus
    freed_bytes: int
    target: str | None = None
    error_code: str | None = None
    message: str | None = None
