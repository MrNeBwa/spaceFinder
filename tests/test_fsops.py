from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from spacefinder.core import fsops
from spacefinder.core.path_policy import PathPolicy


def test_safe_rmtree_refuses_nested_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "home" / ".cache"
    target = cache / "big"
    nested = target / "mounted"
    nested.mkdir(parents=True)
    policy = PathPolicy(
        allowed_roots=[cache],
        protected=[tmp_path / "home"],
        test_root=tmp_path,
    )

    real_ismount = os.path.ismount
    monkeypatch.setattr(
        os.path,
        "ismount",
        lambda path: Path(path) == nested or real_ismount(path),
    )

    with pytest.raises(OSError) as exc_info:
        fsops.safe_rmtree(target, policy)

    assert exc_info.value.errno == errno.EBUSY
    assert target.exists()


def test_safe_unlink_removes_symlink_not_target(tmp_path: Path) -> None:
    cache = tmp_path / "home" / ".cache"
    cache.mkdir(parents=True)
    target = cache / "target"
    target.write_text("keep", encoding="utf-8")
    link = cache / "link"
    link.symlink_to(target)
    policy = PathPolicy(
        allowed_roots=[cache],
        protected=[tmp_path / "home"],
        test_root=tmp_path,
    )

    fsops.safe_unlink(link, policy)

    assert not link.exists()
    assert target.read_text(encoding="utf-8") == "keep"


def test_safe_rmtree_refuses_symlink_to_directory(tmp_path: Path) -> None:
    cache = tmp_path / "home" / ".cache"
    cache.mkdir(parents=True)
    target = tmp_path / "target"
    target.mkdir()
    (target / "keep").write_text("keep", encoding="utf-8")
    link = cache / "link"
    link.symlink_to(target, target_is_directory=True)
    policy = PathPolicy(
        allowed_roots=[cache],
        protected=[tmp_path / "home"],
        test_root=tmp_path,
    )

    with pytest.raises(NotADirectoryError):
        fsops.safe_rmtree(link, policy)

    assert (target / "keep").exists()
