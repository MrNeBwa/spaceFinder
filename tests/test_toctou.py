from __future__ import annotations

import time
from pathlib import Path

from spacefinder.core.deleter import DeleteService
from spacefinder.core.history import HistoryLog
from spacefinder.core.models import (
    DeleteMode,
    DeleteStatus,
    Item,
    ItemKind,
    Progress,
    RiskLevel,
    ScanSession,
    ScanStatus,
)
from spacefinder.core.path_policy import PathPolicy


class FakeTrash:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def contains(self, path: Path) -> bool:
        return False

    def trash(self, path: Path) -> Path:
        self.calls.append(path)
        return path.parent / "trash" / path.name

    def expunge(self, path: Path) -> None:
        self.calls.append(path)


def make_item(path: Path) -> Item:
    st = path.lstat()
    return Item(
        id=path.name,
        rule_id="cache",
        path=path,
        kind=ItemKind.DIRECTORY if path.is_dir() else ItemKind.FILE,
        size_bytes=st.st_blocks * 512,
        apparent_bytes=st.st_size,
        mtime=st.st_mtime,
        risk=RiskLevel.LOW,
        dev=st.st_dev,
        ino=st.st_ino,
    )


def make_service(tmp_path: Path, policy: PathPolicy, trash: FakeTrash) -> DeleteService:
    return DeleteService(
        trash,  # type: ignore[arg-type]
        policy,
        HistoryLog(tmp_path / "state" / "audit.jsonl"),
    )


def make_session(item: Item) -> ScanSession:
    session = ScanSession(
        id="scan",
        created_at=time.time(),
        status=ScanStatus.DONE,
        progress=Progress(),
    )
    session.items[item.id] = item
    session.paths.add(str(item.path))
    return session


def test_delete_refuses_file_replaced_after_scan(tmp_path: Path) -> None:
    cache = tmp_path / "home" / ".cache"
    cache.mkdir(parents=True)
    path = cache / "blob"
    path.write_text("old", encoding="utf-8")
    item = make_item(path)
    policy = PathPolicy(
        allowed_roots=[cache],
        protected=[tmp_path / "home"],
        test_root=tmp_path,
    )
    trash = FakeTrash()

    replacement = cache / "replacement"
    replacement.write_text("new", encoding="utf-8")
    replacement.replace(path)

    results = make_service(tmp_path, policy, trash).delete(
        make_session(item), [item.id], DeleteMode.TRASH, True
    )

    assert results[0].status is DeleteStatus.FAILED
    assert results[0].error_code == "changed"
    assert path.read_text(encoding="utf-8") == "new"
    assert trash.calls == []


def test_delete_refuses_directory_replaced_after_scan(tmp_path: Path) -> None:
    cache = tmp_path / "home" / ".cache"
    cache.mkdir(parents=True)
    path = cache / "pip"
    path.mkdir()
    item = make_item(path)
    policy = PathPolicy(
        allowed_roots=[cache],
        protected=[tmp_path / "home"],
        test_root=tmp_path,
    )
    trash = FakeTrash()

    replacement = cache / "pip-old"
    path.rename(replacement)
    path.mkdir()
    (path / "new").write_text("new", encoding="utf-8")

    results = make_service(tmp_path, policy, trash).delete(
        make_session(item), [item.id], DeleteMode.TRASH, True
    )

    assert results[0].status is DeleteStatus.FAILED
    assert results[0].error_code == "changed"
    assert (path / "new").exists()
    assert replacement.exists()
    assert trash.calls == []


def test_dry_run_reports_replaced_object(tmp_path: Path) -> None:
    cache = tmp_path / "home" / ".cache"
    cache.mkdir(parents=True)
    path = cache / "blob"
    path.write_text("old", encoding="utf-8")
    item = make_item(path)
    policy = PathPolicy(
        allowed_roots=[cache],
        protected=[tmp_path / "home"],
        test_root=tmp_path,
    )

    replacement = cache / "replacement"
    replacement.write_text("new", encoding="utf-8")
    replacement.replace(path)

    service = DeleteService(
        FakeTrash(),  # type: ignore[arg-type]
        policy,
        HistoryLog(tmp_path / "state" / "audit.jsonl"),
        dry_run=True,
    )
    results = service.delete(
        make_session(item), [item.id], DeleteMode.TRASH, True
    )

    assert results[0].status is DeleteStatus.FAILED
    assert results[0].error_code == "changed"
    assert path.read_text(encoding="utf-8") == "new"
