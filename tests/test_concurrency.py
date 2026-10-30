from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from spacefinder.core.deleter import DeleteService, DeleteValidationError
from spacefinder.core.history import HistoryLog
from spacefinder.core.models import (
    DeleteMode,
    Item,
    ItemKind,
    Progress,
    RiskLevel,
    ScanSession,
    ScanStatus,
)
from spacefinder.core.path_policy import PathPolicy


class FakeTrash:
    def contains(self, path: Path) -> bool:
        return False

    def trash(self, path: Path) -> Path:
        return path.parent / "trash" / path.name

    def expunge(self, path: Path) -> None:
        return None


class LockProbeTrash(FakeTrash):
    """Проверяет из отдельного потока, свободен ли session.lock."""

    def __init__(self, session: ScanSession) -> None:
        self.session = session
        self.lock_was_free = False

    def trash(self, path: Path) -> Path:
        acquired: list[bool] = []

        def probe() -> None:
            got = self.session.lock.acquire(timeout=0.2)
            acquired.append(got)
            if got:
                self.session.lock.release()

        thread = threading.Thread(target=probe)
        thread.start()
        thread.join()
        self.lock_was_free = acquired == [True]
        return super().trash(path)


class BlockingTrash(FakeTrash):
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def trash(self, path: Path) -> Path:
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("тест не отпустил удаление")
        return super().trash(path)


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


def make_service(
    tmp_path: Path, policy: PathPolicy, trash: FakeTrash
) -> DeleteService:
    return DeleteService(
        trash,  # type: ignore[arg-type]
        policy,
        HistoryLog(tmp_path / "state" / "audit.jsonl"),
    )


def test_delete_does_not_hold_session_lock_during_io(tmp_path: Path) -> None:
    cache = tmp_path / "home" / ".cache"
    cache.mkdir(parents=True)
    path = cache / "pip"
    path.mkdir()
    item = make_item(path)
    session = make_session(item)
    policy = PathPolicy(
        allowed_roots=[cache],
        protected=[tmp_path / "home"],
        test_root=tmp_path,
    )
    trash = LockProbeTrash(session)

    results = make_service(tmp_path, policy, trash).delete(
        session, [item.id], DeleteMode.TRASH, True
    )

    assert results[0].status.value == "trashed"
    assert trash.lock_was_free is True


def test_parallel_delete_in_same_session_returns_conflict(tmp_path: Path) -> None:
    cache = tmp_path / "home" / ".cache"
    cache.mkdir(parents=True)
    path = cache / "pip"
    path.mkdir()
    item = make_item(path)
    session = make_session(item)
    policy = PathPolicy(
        allowed_roots=[cache],
        protected=[tmp_path / "home"],
        test_root=tmp_path,
    )
    trash = BlockingTrash()
    service = make_service(tmp_path, policy, trash)
    errors: list[BaseException] = []

    def first_delete() -> None:
        try:
            service.delete(session, [item.id], DeleteMode.TRASH, True)
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=first_delete)
    worker.start()
    assert trash.entered.wait(timeout=2)

    with pytest.raises(DeleteValidationError) as exc_info:
        service.delete(session, [item.id], DeleteMode.TRASH, True)

    assert exc_info.value.code == "delete_in_progress"
    trash.release.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert errors == []
    assert item.id in session.deleted_ids
