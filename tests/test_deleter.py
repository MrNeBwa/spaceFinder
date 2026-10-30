from __future__ import annotations

import time
from pathlib import Path

import pytest

from spacefinder.core.deleter import DeleteService, DeleteValidationError
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
    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[Path] = []
        self.fail_on = fail_on
        self.contents: set[Path] = set()

    def contains(self, path: Path) -> bool:
        return path in self.contents

    def trash(self, path: Path) -> Path:
        self.calls.append(path)
        if self.fail_on and path.name == self.fail_on:
            raise OSError(5, "I/O error")
        return path.parent / "trash" / path.name

    def expunge(self, path: Path) -> None:
        self.calls.append(path)
        self.contents.discard(path)


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    path = tmp_path / "home" / ".cache"
    path.mkdir(parents=True)
    return path


@pytest.fixture
def policy(tmp_path: Path, cache: Path) -> PathPolicy:
    return PathPolicy(
        allowed_roots=[cache],
        protected=[tmp_path / "home"],
        test_root=tmp_path,
    )


def make_item(path: Path, item_id: str | None = None) -> Item:
    stat = path.lstat()
    kind = ItemKind.DIRECTORY if path.is_dir() else ItemKind.FILE
    return Item(
        id=item_id or path.name,
        rule_id="cache",
        path=path,
        kind=kind,
        size_bytes=stat.st_blocks * 512,
        apparent_bytes=stat.st_size,
        mtime=stat.st_mtime,
        risk=RiskLevel.LOW,
        dev=stat.st_dev,
        ino=stat.st_ino,
    )


def make_session(items: list[Item]) -> ScanSession:
    session = ScanSession(
        id="scan",
        created_at=time.time(),
        status=ScanStatus.DONE,
        progress=Progress(),
    )
    for item in items:
        session.items[item.id] = item
        session.paths.add(str(item.path))
    return session


def make_service(
    tmp_path: Path,
    policy: PathPolicy,
    trash: FakeTrash,
    *,
    dry_run: bool = False,
    allow_permanent: bool = False,
) -> DeleteService:
    return DeleteService(
        trash,  # type: ignore[arg-type]
        policy,
        HistoryLog(tmp_path / "state" / "audit.jsonl"),
        dry_run=dry_run,
        allow_permanent=allow_permanent,
    )


def test_dry_run_does_not_touch_files(
    tmp_path: Path, cache: Path, policy: PathPolicy
) -> None:
    path = cache / "pip"
    path.mkdir()
    item = make_item(path)
    session = make_session([item])
    trash = FakeTrash()
    service = make_service(tmp_path, policy, trash, dry_run=True)

    results = service.delete(session, [item.id], DeleteMode.TRASH, True)

    assert results[0].status is DeleteStatus.SIMULATED
    assert path.exists()
    assert trash.calls == []
    assert session.deleted_ids == set()


def test_trash_mode_calls_trash(
    tmp_path: Path, cache: Path, policy: PathPolicy
) -> None:
    path = cache / "pip"
    path.mkdir()
    item = make_item(path)
    session = make_session([item])
    trash = FakeTrash()
    service = make_service(tmp_path, policy, trash)

    results = service.delete(session, [item.id], DeleteMode.TRASH, True)

    assert results[0].status is DeleteStatus.TRASHED
    assert results[0].freed_bytes == 0
    assert trash.calls == [path]
    assert item.id in session.deleted_ids


def test_permanent_requires_flag(
    tmp_path: Path, cache: Path, policy: PathPolicy
) -> None:
    path = cache / "pip"
    path.mkdir()
    item = make_item(path)
    session = make_session([item])
    service = make_service(tmp_path, policy, FakeTrash())

    with pytest.raises(DeleteValidationError) as exc_info:
        service.delete(session, [item.id], DeleteMode.PERMANENT, True)

    assert exc_info.value.code == "permanent_disabled"
    assert path.exists()


def test_requires_confirmation(tmp_path: Path, cache: Path, policy: PathPolicy) -> None:
    path = cache / "pip"
    path.mkdir()
    item = make_item(path)
    service = make_service(tmp_path, policy, FakeTrash())

    with pytest.raises(DeleteValidationError) as exc_info:
        service.delete(make_session([item]), [item.id], DeleteMode.TRASH, False)

    assert exc_info.value.code == "confirmation_required"


def test_repeated_delete_is_conflict(
    tmp_path: Path, cache: Path, policy: PathPolicy
) -> None:
    path = cache / "pip"
    path.mkdir()
    item = make_item(path)
    session = make_session([item])
    session.deleted_ids.add(item.id)
    service = make_service(tmp_path, policy, FakeTrash())

    with pytest.raises(DeleteValidationError) as exc_info:
        service.delete(session, [item.id], DeleteMode.TRASH, True)

    assert exc_info.value.code == "conflict"


def test_overlapping_selection_is_conflict(
    tmp_path: Path, cache: Path, policy: PathPolicy
) -> None:
    parent = cache / "pip"
    parent.mkdir()
    child = parent / "blob"
    child.write_text("x", encoding="utf-8")
    parent_item = make_item(parent)
    child_item = make_item(child)
    session = make_session([parent_item, child_item])
    service = make_service(tmp_path, policy, FakeTrash(), dry_run=True)

    with pytest.raises(DeleteValidationError) as exc_info:
        service.delete(
            session,
            [parent_item.id, child_item.id],
            DeleteMode.TRASH,
            True,
        )

    assert exc_info.value.code == "conflict"


def test_missing_file_is_skipped(
    tmp_path: Path, cache: Path, policy: PathPolicy
) -> None:
    path = cache / "pip"
    path.mkdir()
    item = make_item(path)
    session = make_session([item])
    path.rmdir()
    service = make_service(tmp_path, policy, FakeTrash())

    results = service.delete(session, [item.id], DeleteMode.TRASH, True)

    assert results[0].status is DeleteStatus.SKIPPED
    assert results[0].freed_bytes == 0


def test_partial_failure_keeps_other_results(
    tmp_path: Path, cache: Path, policy: PathPolicy
) -> None:
    good = cache / "good"
    good.mkdir()
    bad = cache / "bad"
    bad.mkdir()
    items = [make_item(good), make_item(bad)]
    session = make_session(items)
    trash = FakeTrash(fail_on="bad")
    service = make_service(tmp_path, policy, trash)

    results = service.delete(
        session, [item.id for item in items], DeleteMode.TRASH, True
    )

    by_id = {result.item_id: result for result in results}
    assert by_id["good"].status is DeleteStatus.TRASHED
    assert by_id["bad"].status is DeleteStatus.FAILED
    assert by_id["bad"].error_code.startswith("errno_")


def test_items_already_in_trash_are_deleted_permanently(
    tmp_path: Path, cache: Path
) -> None:
    trash_files = tmp_path / "data" / "Trash" / "files"
    item_path = trash_files / "old"
    item_path.mkdir(parents=True)
    (item_path / "blob").write_text("x", encoding="utf-8")

    policy = PathPolicy(
        allowed_roots=[cache, trash_files],
        protected=[tmp_path / "home"],
        test_root=tmp_path,
    )
    from spacefinder.core.trash import TrashService

    trash = TrashService(tmp_path / "data", policy)
    item = make_item(item_path)
    session = make_session([item])
    service = make_service(tmp_path, policy, trash)  # type: ignore[arg-type]

    results = service.delete(session, [item.id], DeleteMode.TRASH, True)

    assert results[0].status is DeleteStatus.DELETED
    assert not item_path.exists()


def test_permanent_mode_on_trash_item_removes_metadata(
    tmp_path: Path, cache: Path
) -> None:
    trash_files = tmp_path / "data" / "Trash" / "files"
    info_dir = tmp_path / "data" / "Trash" / "info"
    item_path = trash_files / "old"
    item_path.mkdir(parents=True)
    (item_path / "blob").write_text("x", encoding="utf-8")
    info_dir.mkdir(parents=True)
    info_path = info_dir / "old.trashinfo"
    info_path.write_text("[Trash Info]\nPath=/tmp/old\n", encoding="utf-8")

    from spacefinder.core.trash import TrashService

    trash_policy = PathPolicy(
        allowed_roots=[cache, trash_files],
        protected=[tmp_path / "home"],
        test_root=tmp_path,
    )
    item = make_item(item_path)
    session = make_session([item])
    service = make_service(
        tmp_path,
        trash_policy,
        TrashService(tmp_path / "data", trash_policy),
        allow_permanent=True,
    )

    results = service.delete(session, [item.id], DeleteMode.PERMANENT, True)

    assert results[0].status is DeleteStatus.DELETED
    assert not item_path.exists()
    assert not info_path.exists()


def test_permanent_mode_deletes_object(
    tmp_path: Path, cache: Path, policy: PathPolicy
) -> None:
    path = cache / "blob"
    path.write_text("x" * 100, encoding="utf-8")
    item = make_item(path)
    session = make_session([item])
    service = make_service(tmp_path, policy, FakeTrash(), allow_permanent=True)

    results = service.delete(session, [item.id], DeleteMode.PERMANENT, True)

    assert results[0].status is DeleteStatus.DELETED
    assert results[0].freed_bytes == item.size_bytes
    assert not path.exists()
    assert item.id in session.deleted_ids


def test_audit_log_written(tmp_path: Path, cache: Path, policy: PathPolicy) -> None:
    path = cache / "pip"
    path.mkdir()
    item = make_item(path)
    service = make_service(tmp_path, policy, FakeTrash(), dry_run=True)

    service.delete(make_session([item]), [item.id], DeleteMode.TRASH, True)

    entries = service.history.tail(10)
    assert len(entries) == 1
    assert entries[0]["status"] == "simulated"
    assert entries[0]["path"] == str(path)
