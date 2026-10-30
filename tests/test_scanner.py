from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from spacefinder.core.models import (
    Progress,
    RiskLevel,
    ScanRule,
    ScanSession,
    ScanStatus,
)
from spacefinder.core.scanner import Scanner


def make_rule(rule_id: str, root: Path, **kwargs) -> ScanRule:
    values = {
        "id": rule_id,
        "title": rule_id,
        "description": "",
        "risk": RiskLevel.LOW,
        "default_selected": True,
        "roots": (root,),
        "selector": "children",
        "min_age_days": 0,
    }
    values.update(kwargs)
    return ScanRule(**values)


def make_session() -> ScanSession:
    return ScanSession(
        id="test-scan",
        created_at=time.time(),
        status=ScanStatus.QUEUED,
        progress=Progress(),
    )


def run_scan(
    rules: tuple[ScanRule, ...],
    session: ScanSession | None = None,
    **scanner_kwargs,
) -> ScanSession:
    session = session or make_session()
    Scanner(rules, **scanner_kwargs).scan(session)
    return session


def test_scans_children_and_measures_size(tmp_path: Path) -> None:
    cache = tmp_path / ".cache"
    pip = cache / "pip"
    pip.mkdir(parents=True)
    (pip / "blob").write_bytes(b"x" * 5000)

    session = run_scan((make_rule("cache", cache),))

    assert session.status is ScanStatus.DONE
    assert len(session.items) == 1
    item = next(iter(session.items.values()))
    assert item.path == pip
    assert item.apparent_bytes == 5000
    assert item.size_bytes > 0


def test_min_age_filters_new_entries(tmp_path: Path, make_old) -> None:
    cache = tmp_path / ".cache"
    old = cache / "old"
    new = cache / "new"
    old.mkdir(parents=True)
    new.mkdir()
    make_old(old, days=40)

    session = run_scan((make_rule("cache", cache, min_age_days=30),))

    assert [item.path.name for item in session.items.values()] == ["old"]


def test_glob_rule_filters_by_pattern_and_age(tmp_path: Path, make_old) -> None:
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    old_iso = downloads / "old.iso"
    new_iso = downloads / "new.iso"
    old_txt = downloads / "notes.txt"
    for path in (old_iso, new_iso, old_txt):
        path.write_text("x", encoding="utf-8")
    make_old(old_iso, days=40)
    make_old(old_txt, days=40)
    make_old(downloads, days=40)

    rule = make_rule(
        "downloads",
        downloads,
        selector="glob",
        patterns=("*.iso",),
        min_age_days=30,
        risk=RiskLevel.HIGH,
    )
    session = run_scan((rule,))

    assert [item.path.name for item in session.items.values()] == ["old.iso"]


def test_symlink_candidate_is_skipped(tmp_path: Path) -> None:
    cache = tmp_path / ".cache"
    cache.mkdir()
    target = tmp_path / "target"
    target.write_bytes(b"x" * 100)
    (cache / "link").symlink_to(target)

    session = run_scan((make_rule("cache", cache),))

    assert session.items == {}


def test_permission_error_becomes_warning(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root игнорирует права доступа")

    cache = tmp_path / ".cache"
    locked = cache / "locked"
    locked.mkdir(parents=True)
    (locked / "secret").write_text("x", encoding="utf-8")
    os.chmod(locked, 0o000)

    try:
        session = run_scan((make_rule("cache", cache),))
    finally:
        os.chmod(locked, 0o700)

    item = next(iter(session.items.values()))
    assert any("Нет доступа" in warning for warning in item.warnings)


def test_cancel_inside_candidate_enumeration(tmp_path: Path) -> None:
    class CancellingScanner(Scanner):
        def _candidates(self, rule, deadline, session):
            session.cancel_event.set()
            return iter(())

    session = make_session()
    CancellingScanner((make_rule("cache", tmp_path / ".cache"),)).scan(session)

    assert session.status is ScanStatus.CANCELLED


def test_cancel_before_start(tmp_path: Path) -> None:
    cache = tmp_path / ".cache"
    cache.mkdir()
    (cache / "pip").mkdir()

    session = make_session()
    session.cancel_event.set()
    session = run_scan((make_rule("cache", cache),), session)

    assert session.status is ScanStatus.CANCELLED
    assert session.items == {}


def test_max_items_truncates(tmp_path: Path) -> None:
    cache = tmp_path / ".cache"
    cache.mkdir()
    for index in range(3):
        (cache / f"item{index}").mkdir()

    session = run_scan((make_rule("cache", cache),), max_items=2)

    assert len(session.items) == 2
    assert session.progress.truncated is True


def test_overlapping_rules_do_not_duplicate_children(tmp_path: Path) -> None:
    cache = tmp_path / ".cache"
    pip = cache / "pip"
    pip.mkdir(parents=True)
    (pip / "blob").write_text("x", encoding="utf-8")

    session = run_scan(
        (
            make_rule("cache", cache),
            make_rule("pip", pip),
        )
    )

    paths = {str(item.path) for item in session.items.values()}
    assert str(pip) in paths
    assert str(pip / "blob") not in paths


def test_missing_root_is_not_an_error(tmp_path: Path) -> None:
    session = run_scan((make_rule("missing", tmp_path / "nope"),))
    assert session.status is ScanStatus.DONE
    assert session.items == {}


def test_same_relative_name_in_two_roots_gets_unique_ids(tmp_path: Path) -> None:
    first_root = tmp_path / "root-a"
    second_root = tmp_path / "root-b"
    (first_root / "same").mkdir(parents=True)
    (second_root / "same").mkdir(parents=True)

    rule = make_rule("multi", first_root, roots=(first_root, second_root))
    session = run_scan((rule,))

    assert len(session.items) == 2
    assert {str(item.path) for item in session.items.values()} == {
        str(first_root / "same"),
        str(second_root / "same"),
    }


def test_same_path_from_two_rules_is_counted_once(tmp_path: Path) -> None:
    cache = tmp_path / ".cache"
    pip = cache / "pip"
    pip.mkdir(parents=True)
    (pip / "blob").write_text("x", encoding="utf-8")

    session = run_scan(
        (
            make_rule("cache-first", cache),
            make_rule("cache-second", cache),
        )
    )

    assert [item.path for item in session.items.values()] == [pip]


def test_mount_candidate_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / ".cache"
    mounted = cache / "mounted"
    mounted.mkdir(parents=True)
    (mounted / "remote").write_text("x", encoding="utf-8")

    real_ismount = os.path.ismount
    monkeypatch.setattr(
        os.path,
        "ismount",
        lambda path: Path(path) == mounted or real_ismount(path),
    )

    session = run_scan((make_rule("cache", cache),))

    assert session.items == {}
    assert any("монтирования" in warning for warning in session.warnings)


def test_nested_mount_is_not_traversed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / ".cache"
    big = cache / "big"
    mounted = big / "mounted"
    mounted.mkdir(parents=True)
    (big / "local").write_text("x" * 100, encoding="utf-8")
    (mounted / "remote").write_text("y" * 5000, encoding="utf-8")

    real_ismount = os.path.ismount
    monkeypatch.setattr(
        os.path,
        "ismount",
        lambda path: Path(path) == mounted or real_ismount(path),
    )

    session = run_scan((make_rule("cache", cache),))

    item = next(iter(session.items.values()))
    assert item.path == big
    assert item.apparent_bytes == 100
    assert any("монтирования" in warning for warning in item.warnings)


def test_item_keeps_inode_identity(tmp_path: Path) -> None:
    cache = tmp_path / ".cache"
    pip = cache / "pip"
    pip.mkdir(parents=True)
    (pip / "blob").write_text("x", encoding="utf-8")

    session = run_scan((make_rule("cache", cache),))

    item = next(iter(session.items.values()))
    st = pip.lstat()
    assert item.dev == st.st_dev
    assert item.ino == st.st_ino


def test_non_utf8_filename_is_scanned(tmp_path: Path) -> None:
    cache = tmp_path / ".cache"
    cache.mkdir()
    raw = os.fsencode(str(cache)) + b"/bad-\xff"
    fd = os.open(raw, os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(fd, b"x")
    finally:
        os.close(fd)

    session = run_scan((make_rule("cache", cache),))

    assert len(session.items) == 1
    assert next(iter(session.items.values())).apparent_bytes == 1


def test_session_warnings_are_capped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / ".cache"
    cache.mkdir()
    for index in range(150):
        (cache / f"item{index}").mkdir()

    monkeypatch.setattr(os.path, "ismount", lambda _path: True)
    session = run_scan((make_rule("cache", cache),))

    assert len(session.warnings) <= 100


def test_timeout_marks_results_truncated(tmp_path: Path) -> None:
    cache = tmp_path / ".cache"
    (cache / "pip").mkdir(parents=True)

    session = run_scan((make_rule("cache", cache),), max_seconds=1e-12)

    assert session.progress.truncated is True


def test_timeout_after_measure_does_not_add_partial_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / ".cache"
    (cache / "pip").mkdir(parents=True)
    now = [0.0]

    def slow_measure(*_args, **_kwargs):
        now[0] = 200.0
        return 100, 100

    monkeypatch.setattr("spacefinder.core.scanner.measure_path", slow_measure)

    session = run_scan(
        (make_rule("cache", cache),),
        max_seconds=100,
        clock=lambda: now[0],
    )

    assert session.items == {}
    assert session.progress.truncated is True
