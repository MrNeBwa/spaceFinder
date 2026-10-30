from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spacefinder.app import create_app
from spacefinder.config import AppConfig
from spacefinder.core import fsops


@pytest.fixture(autouse=True)
def no_real_deletes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Запретить любые деструктивные операции вне tmp_path.

    Тесты не должны удалять пользовательские файлы даже случайно.
    """

    sandbox = tmp_path.resolve()

    def within(path: object) -> bool:
        try:
            Path(path).resolve().relative_to(sandbox)
            return True
        except (ValueError, OSError):
            return False

    def require(*paths: object) -> None:
        for path in paths:
            if not within(path):
                raise AssertionError(f"Попытка удаления вне тестовой песочницы: {path}")

    real_move = fsops.safe_move
    real_rmtree = fsops.safe_rmtree
    real_unlink = fsops.safe_unlink

    def guarded_move(src, dst, policy):
        require(src, dst)
        return real_move(src, dst, policy)

    def guarded_rmtree(path, policy):
        require(path)
        return real_rmtree(path, policy)

    def guarded_unlink(path, policy):
        require(path)
        return real_unlink(path, policy)

    monkeypatch.setattr(fsops, "safe_move", guarded_move)
    monkeypatch.setattr(fsops, "safe_rmtree", guarded_rmtree)
    monkeypatch.setattr(fsops, "safe_unlink", guarded_unlink)
    monkeypatch.setenv("SPACEFINDER_TEST_MODE", "1")
    yield


@pytest.fixture
def app_factory(tmp_path: Path):
    def factory(**overrides: object):
        home = tmp_path / "home"
        for directory in (
            home / ".cache",
            home / ".npm" / "_cacache",
            home / "Downloads",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        config = AppConfig(
            home=home,
            data_home=tmp_path / "data",
            state_home=tmp_path / "state",
            test_mode=True,
            test_root=tmp_path,
            dry_run=True,
            port=8765,
        )
        for key, value in overrides.items():
            setattr(config, key, value)
        return create_app(config)

    return factory


@pytest.fixture
def client(app_factory):
    app = app_factory()
    with TestClient(app, base_url="http://127.0.0.1:8765") as test_client:
        yield test_client


@pytest.fixture
def make_old():
    def make(path: Path, days: int = 40) -> None:
        timestamp = 1_700_000_000 - days * 86_400
        os.utime(path, (timestamp, timestamp))

    return make
