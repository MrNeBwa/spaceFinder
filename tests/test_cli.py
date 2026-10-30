from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from spacefinder.cli import main


def test_version_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


def test_scan_json_in_sandbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    cache = home / ".cache" / "pip"
    cache.mkdir(parents=True)
    (cache / "blob").write_bytes(b"x" * 4096)
    timestamp = time.time() - 40 * 86_400
    os.utime(cache, (timestamp, timestamp))
    monkeypatch.setenv("SPACEFINDER_HOME", str(home))
    monkeypatch.setenv("SPACEFINDER_TEST_ROOT", str(tmp_path))

    assert main(["scan", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "done"
    assert payload["items"][0]["path"] == "~/.cache/pip"


def test_non_local_host_refused(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SPACEFINDER_HOME", "/tmp/spacefinder-cli-test")
    assert main(["serve", "--host", "0.0.0.0", "--no-browser"]) == 2
    assert "локальный host" in capsys.readouterr().err


def test_unknown_rule_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SPACEFINDER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SPACEFINDER_TEST_ROOT", str(tmp_path))

    assert main(["scan", "--rule", "does-not-exist"]) == 2
    assert "Неизвестное правило" in capsys.readouterr().err


def test_privileged_rule_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SPACEFINDER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SPACEFINDER_TEST_ROOT", str(tmp_path))

    assert main(["scan", "--rule", "system_info"]) == 2
    assert "root-прав" in capsys.readouterr().err


def test_scan_error_returns_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from spacefinder.core.scanner import Scanner

    def failing_scan(self, session, rule_ids=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(Scanner, "scan", failing_scan)
    monkeypatch.setenv("SPACEFINDER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SPACEFINDER_TEST_ROOT", str(tmp_path))

    assert main(["scan"]) == 1
    assert "Ошибка сканирования" in capsys.readouterr().err
