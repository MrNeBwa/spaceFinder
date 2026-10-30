from __future__ import annotations

from pathlib import Path

import pytest

from spacefinder.app import create_app
from spacefinder.config import AppConfig


def test_invalid_port_has_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPACEFINDER_PORT", "not-a-number")
    with pytest.raises(ValueError, match="SPACEFINDER_PORT"):
        AppConfig.from_env()


def test_relative_home_is_resolved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SPACEFINDER_HOME", "demo")

    config = AppConfig.from_env()

    assert config.home == (tmp_path / "demo").resolve()
    assert config.data_home == config.home / ".local" / "share"
    assert config.state_home == config.home / ".local" / "state"


def test_negative_timeout_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPACEFINDER_SCAN_TIMEOUT", "-1")
    with pytest.raises(ValueError, match="SPACEFINDER_SCAN_TIMEOUT"):
        AppConfig.from_env()


def test_non_local_host_rejected() -> None:
    with pytest.raises(ValueError, match="локальный host"):
        AppConfig(host="0.0.0.0")


def test_paths_are_resolved_to_absolute(tmp_path: Path) -> None:
    config = AppConfig(
        home=tmp_path / "home",
        data_home=tmp_path / "data",
        state_home=tmp_path / "state",
    )

    assert config.home.is_absolute()
    assert config.data_home.is_absolute()
    assert config.state_home.is_absolute()


def test_max_items_has_an_upper_bound() -> None:
    with pytest.raises(ValueError, match="100000"):
        AppConfig(max_items=100_001)


def test_test_mode_requires_test_root(tmp_path: Path) -> None:
    config = AppConfig(
        home=tmp_path,
        data_home=tmp_path,
        state_home=tmp_path,
        test_mode=True,
    )

    with pytest.raises(ValueError, match="test_root"):
        create_app(config)


def test_test_mode_rejects_paths_outside_sandbox(tmp_path: Path) -> None:
    config = AppConfig(
        home=tmp_path / "home",
        data_home=tmp_path / "data",
        state_home=tmp_path / "state",
        test_mode=True,
        test_root=tmp_path / "sandbox",
    )

    with pytest.raises(ValueError, match="test_mode"):
        create_app(config)


def test_test_root_cannot_be_filesystem_root(tmp_path: Path) -> None:
    config = AppConfig(
        home=tmp_path,
        data_home=tmp_path,
        state_home=tmp_path,
        test_mode=True,
        test_root=Path("/"),
    )

    with pytest.raises(ValueError, match="test_root"):
        create_app(config)
