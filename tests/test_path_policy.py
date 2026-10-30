from __future__ import annotations

from pathlib import Path

import pytest

from spacefinder.core.path_policy import PathPolicy, ProtectedPathError


@pytest.fixture
def policy(tmp_path: Path) -> PathPolicy:
    cache = tmp_path / "home" / ".cache"
    cache.mkdir(parents=True)
    return PathPolicy(
        allowed_roots=[cache],
        protected=[tmp_path / "home"],
        test_root=tmp_path,
    )


def test_allows_child_of_allowed_root(tmp_path: Path, policy: PathPolicy) -> None:
    candidate = tmp_path / "home" / ".cache" / "pip"
    assert policy.ensure_deletable(candidate) == candidate


def test_rejects_rule_root_itself(tmp_path: Path, policy: PathPolicy) -> None:
    with pytest.raises(ProtectedPathError) as exc_info:
        policy.ensure_deletable(tmp_path / "home" / ".cache")
    assert exc_info.value.code == "protected_path"


def test_rejects_outside_allowed_roots(tmp_path: Path, policy: PathPolicy) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ProtectedPathError) as exc_info:
        policy.ensure_deletable(outside / "file")
    assert exc_info.value.code == "outside_allowed_roots"


def test_rejects_relative_path(policy: PathPolicy) -> None:
    with pytest.raises(ProtectedPathError) as exc_info:
        policy.ensure_deletable(Path("relative/path"))
    assert exc_info.value.code == "invalid_path"


def test_rejects_escape_through_symlink_directory(
    tmp_path: Path, policy: PathPolicy
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "home" / ".cache" / "link"
    link.symlink_to(outside)

    with pytest.raises(ProtectedPathError) as exc_info:
        policy.ensure_deletable(link / "file")
    assert exc_info.value.code == "outside_allowed_roots"


def test_rejects_protected_subtree(tmp_path: Path) -> None:
    cache = tmp_path / "home" / ".cache"
    secret = cache / "secret"
    secret.mkdir(parents=True)
    guarded = PathPolicy(
        allowed_roots=[cache],
        protected=[tmp_path / "home"],
        protected_subtrees=[secret],
        test_root=tmp_path,
    )

    with pytest.raises(ProtectedPathError) as exc_info:
        guarded.ensure_deletable(secret / "file")
    assert exc_info.value.code == "protected_path"


def test_rejects_allowed_root_equal_to_protected(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(ValueError):
        PathPolicy(allowed_roots=[home], protected=[home], test_root=tmp_path)


def test_rejects_allowed_root_inside_protected_subtree(tmp_path: Path) -> None:
    home = tmp_path / "home"
    secret = home / ".ssh"
    nested = secret / "nested"
    nested.mkdir(parents=True)
    with pytest.raises(ValueError):
        PathPolicy(
            allowed_roots=[nested],
            protected=[home],
            protected_subtrees=[secret],
            test_root=tmp_path,
        )


def test_rejects_filesystem_root_as_allowed(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        PathPolicy(allowed_roots=[Path("/")], protected=[], test_root=tmp_path)


def test_test_root_limits_operations(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    sandbox = PathPolicy(
        allowed_roots=[tmp_path],
        protected=[],
        test_root=allowed,
    )
    with pytest.raises(ProtectedPathError) as exc_info:
        sandbox.ensure_deletable(tmp_path / "outside")
    assert exc_info.value.code == "protected_path"


def test_rejects_nul_byte(policy: PathPolicy) -> None:
    with pytest.raises(ProtectedPathError) as exc_info:
        policy.ensure_deletable(Path("/tmp/bad\x00name"))

    assert exc_info.value.code == "invalid_path"
