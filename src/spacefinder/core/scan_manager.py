"""Управление сессиями сканирования и фоновыми потоками."""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from .format import safe_text
from .models import Progress, ScanSession, ScanStatus
from .scanner import Scanner


class ScanManager:
    def __init__(
        self,
        scanner: Scanner,
        ttl: float = 900.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.scanner = scanner
        self.ttl = ttl
        self._clock = clock
        self._sessions: dict[str, ScanSession] = {}
        self._lock = threading.RLock()
        # Один воркер: два одновременных обхода ~/.cache только мешают
        # друг другу и создают лишнюю нагрузку на диск.
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="spacefinder-scan"
        )

    def new_session(self) -> ScanSession:
        return ScanSession(
            id=secrets.token_urlsafe(12),
            created_at=self._clock(),
            status=ScanStatus.QUEUED,
            progress=Progress(),
        )

    def _purge_locked(self) -> None:
        now = self._clock()
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if session.finished_at is not None and now - session.finished_at > self.ttl
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)

    def start(self, rule_ids: list[str] | None = None) -> ScanSession:
        with self._lock:
            self._purge_locked()
            session = self.new_session()
            self._sessions[session.id] = session
            self._executor.submit(self._run, session, rule_ids)
        return session

    def _run(self, session: ScanSession, rule_ids: list[str] | None) -> None:
        try:
            self.scanner.scan(session, rule_ids)
        except Exception as exc:  # Поток не должен падать молча.
            with session.lock:
                session.status = ScanStatus.FAILED
                session.progress.phase = "failed"
                session.warnings.append(
                    safe_text(f"Внутренняя ошибка сканирования: {exc}")
                )
                if session.finished_at is None:
                    session.finished_at = self._clock()

    def get(self, session_id: str) -> ScanSession:
        with self._lock:
            self._purge_locked()
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    def cancel(self, session_id: str) -> ScanSession:
        session = self.get(session_id)
        session.cancel_event.set()
        return session

    def shutdown(self) -> None:
        """Отменить активные сканы и дождаться завершения воркера."""

        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            session.cancel_event.set()
            with session.lock:
                if session.status is ScanStatus.QUEUED:
                    session.status = ScanStatus.CANCELLED
                    session.progress.phase = "cancelled"
                    session.finished_at = self._clock()
        self._executor.shutdown(wait=True, cancel_futures=True)
