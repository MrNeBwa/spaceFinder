from __future__ import annotations

import threading
import time

from spacefinder.core.models import ScanSession, ScanStatus
from spacefinder.core.scan_manager import ScanManager


class BlockingScanner:
    """Сканер, который ждёт отмены, чтобы проверить shutdown."""

    def __init__(self) -> None:
        self.started = threading.Event()

    def scan(self, session: ScanSession, rule_ids: list[str] | None = None) -> None:
        self.started.set()
        session.cancel_event.wait(timeout=5)
        with session.lock:
            session.status = ScanStatus.CANCELLED
            session.progress.phase = "cancelled"
            session.finished_at = time.time()


def test_shutdown_cancels_running_scan() -> None:
    scanner = BlockingScanner()
    manager = ScanManager(scanner, ttl=60)  # type: ignore[arg-type]
    session = manager.start(["user_cache"])
    assert scanner.started.wait(timeout=2)

    manager.shutdown()

    assert session.cancel_event.is_set()
    assert session.status is ScanStatus.CANCELLED
