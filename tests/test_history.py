from __future__ import annotations

import stat
from pathlib import Path

from spacefinder.core.history import HistoryLog


def test_audit_file_is_private(tmp_path: Path) -> None:
    path = tmp_path / "state" / "audit.jsonl"
    log = HistoryLog(path)
    log.append({"status": "simulated"})

    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_malformed_lines_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text(
        '{"status":"deleted"}\n'
        "not json\n"
        '{"status":"trashed"}\n',
        encoding="utf-8",
    )

    entries = HistoryLog(path).tail(10)

    assert [entry["status"] for entry in entries] == ["deleted", "trashed"]


def test_audit_handles_non_utf8_strings(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = HistoryLog(path)
    log.append({"path": "bad\udcff"})

    entries = log.tail(1)

    assert entries[0]["path"] == "bad\udcff"


def test_tail_limit_returns_last_records(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = HistoryLog(path)
    for index in range(5):
        log.append({"index": index})

    entries = log.tail(2)

    assert [entry["index"] for entry in entries] == [3, 4]
