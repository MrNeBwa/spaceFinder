from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from spacefinder.core import fsops
from spacefinder.core.path_policy import PathPolicy, ProtectedPathError
from spacefinder.core.trash import TrashService


@pytest.fixture
def policy(tmp_path: Path) -> PathPolicy:
    cache = tmp_path / "home" / ".cache"
    cache.mkdir(parents=True)
    return PathPolicy(
        allowed_roots=[cache],
        protected=[tmp_path / "home"],
        test_root=tmp_path,
    )


def test_trash_moves_object_and_writes_info(tmp_path: Path, policy: PathPolicy) -> None:
    item = tmp_path / "home" / ".cache" / "pip"
    item.mkdir()
    (item / "blob").write_text("data", encoding="utf-8")

    service = TrashService(tmp_path / "data", policy)
    destination = service.trash(item)

    assert not item.exists()
    assert destination.is_dir()
    assert (destination / "blob").read_text(encoding="utf-8") == "data"

    info = (tmp_path / "data" / "Trash" / "info" / "pip.trashinfo").read_text(
        encoding="utf-8"
    )
    assert info.startswith("[Trash Info]\n")
    assert "Path=" in info
    assert "DeletionDate=" in info


def test_trash_resolves_name_conflicts(tmp_path: Path, policy: PathPolicy) -> None:
    service = TrashService(tmp_path / "data", policy)
    cache = tmp_path / "home" / ".cache"

    first = cache / "pip"
    first.mkdir()
    service.trash(first)

    second = cache / "pip"
    second.mkdir()
    destination = service.trash(second)

    assert destination.name == "pip.1"
    assert (tmp_path / "data" / "Trash" / "info" / "pip.1.trashinfo").exists()


def test_trash_percent_encodes_non_ascii_and_spaces(
    tmp_path: Path, policy: PathPolicy
) -> None:
    item = tmp_path / "home" / ".cache" / "мои файлы"
    item.mkdir()

    service = TrashService(tmp_path / "data", policy)
    service.trash(item)

    info = (tmp_path / "data" / "Trash" / "info" / "мои файлы.trashinfo").read_text(
        encoding="utf-8"
    )
    assert "%20" in info
    assert "%D0" in info


def test_trash_removes_info_when_write_fails(
    tmp_path: Path, policy: PathPolicy, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = tmp_path / "home" / ".cache" / "broken"
    item.mkdir()

    def failing_fsync(fd: int) -> None:
        raise OSError(5, "I/O error")

    monkeypatch.setattr(os, "fsync", failing_fsync)
    service = TrashService(tmp_path / "data", policy)

    with pytest.raises(OSError):
        service.trash(item)

    assert item.exists()
    assert not (tmp_path / "data" / "Trash" / "info" / "broken.trashinfo").exists()


def test_trash_removes_info_when_move_fails(tmp_path: Path, policy: PathPolicy) -> None:
    item = tmp_path / "home" / ".cache" / "broken"
    item.mkdir()

    def failing_move(src, dst, path_policy):
        raise OSError(5, "I/O error")

    service = TrashService(tmp_path / "data", policy, move=failing_move)
    with pytest.raises(OSError):
        service.trash(item)

    assert item.exists()
    assert not (tmp_path / "data" / "Trash" / "info" / "broken.trashinfo").exists()


def test_expunge_removes_file_and_metadata(tmp_path: Path) -> None:
    cache = tmp_path / "home" / ".cache"
    cache.mkdir(parents=True)
    trash_files = tmp_path / "data" / "Trash" / "files"
    policy = PathPolicy(
        allowed_roots=[cache, trash_files],
        protected=[tmp_path / "home"],
        test_root=tmp_path,
    )
    item = cache / "old"
    item.mkdir()
    (item / "blob").write_text("data", encoding="utf-8")

    service = TrashService(tmp_path / "data", policy)
    destination = service.trash(item)
    info_path = tmp_path / "data" / "Trash" / "info" / "old.trashinfo"
    assert destination.exists()
    assert info_path.exists()

    service.expunge(destination)

    assert not destination.exists()
    assert not info_path.exists()


def test_expunge_rejects_path_outside_trash(tmp_path: Path, policy: PathPolicy) -> None:
    item = tmp_path / "home" / ".cache" / "not-in-trash"
    item.mkdir()

    service = TrashService(tmp_path / "data", policy)
    with pytest.raises(ValueError):
        service.expunge(item)


def test_trash_cleans_partial_destination_on_move_failure(tmp_path: Path) -> None:
    cache = tmp_path / "home" / ".cache"
    cache.mkdir(parents=True)
    trash_files = tmp_path / "data" / "Trash" / "files"
    policy = PathPolicy(
        allowed_roots=[cache, trash_files],
        protected=[tmp_path / "home"],
        test_root=tmp_path,
    )
    item = cache / "broken"
    item.mkdir()
    (item / "blob").write_text("data", encoding="utf-8")

    def partial_move(src: Path, dst: Path, _policy: PathPolicy) -> None:
        dst.mkdir(parents=True)
        (dst / "partial").write_text("partial", encoding="utf-8")
        raise OSError(5, "I/O error")

    service = TrashService(tmp_path / "data", policy, move=partial_move)
    with pytest.raises(OSError):
        service.trash(item)

    assert item.exists()
    assert not (trash_files / "broken").exists()
    assert not (tmp_path / "data" / "Trash" / "info" / "broken.trashinfo").exists()


def test_safe_move_falls_back_on_exdev(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "home" / ".cache"
    cache.mkdir(parents=True)
    source = cache / "blob"
    source.write_text("data", encoding="utf-8")
    destination = tmp_path / "data" / "Trash" / "files" / "blob"
    policy = PathPolicy(
        allowed_roots=[cache, destination.parent],
        protected=[tmp_path / "home"],
        test_root=tmp_path,
    )

    def cross_device(_src: object, _dst: object) -> None:
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(os, "replace", cross_device)
    fsops.safe_move(source, destination, policy)

    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "data"


def test_trash_refuses_symlinked_trash_directory(tmp_path: Path) -> None:
    cache = tmp_path / "home" / ".cache"
    cache.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    (data / "Trash").symlink_to(outside, target_is_directory=True)
    item = cache / "pip"
    item.mkdir()

    policy = PathPolicy(
        allowed_roots=[cache, data / "Trash" / "files"],
        protected=[tmp_path / "home"],
        test_root=tmp_path,
    )
    service = TrashService(data, policy)

    with pytest.raises(ProtectedPathError):
        service.trash(item)

    assert item.exists()
    assert not (outside / "files").exists()
