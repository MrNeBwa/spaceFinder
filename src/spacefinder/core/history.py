"""Аудит операций: JSONL с правами 0600."""

from __future__ import annotations

import contextlib
import errno
import json
import os
import threading
from collections import deque
from pathlib import Path
from typing import Any


class HistoryLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            # Не писать аудит через подменённый симлинк: файл должен
            # оставаться внутри state_home.
            flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(self.path, flags, 0o600)
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise OSError(
                        errno.ELOOP,
                        "Файл аудита не должен быть символической ссылкой: "
                        f"{self.path}",
                    ) from exc
                raise
            with contextlib.suppress(OSError):
                os.chmod(self.path, 0o600)
            try:
                # backslashreplace не даёт упасть на surrogateescape-именах:
                # они попадут в JSON как \udcff и корректно прочитаются.
                handle = os.fdopen(
                    fd, "a", encoding="utf-8", errors="backslashreplace"
                )
            except Exception:
                # fdopen не забрал дескриптор — закрываем его сами.
                os.close(fd)
                raise
            with handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())

    def tail(self, limit: int = 50) -> list[dict[str, Any]]:
        if limit <= 0 or not self.path.exists():
            return []
        records: deque[dict[str, Any]] = deque(maxlen=limit)
        with (
            self._lock,
            self.path.open("r", encoding="utf-8", errors="replace") as handle,
        ):
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    records.append(parsed)
        return list(records)
