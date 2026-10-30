"""Сканирование мусорных каталогов с подсчётом занятого места."""

from __future__ import annotations

import hashlib
import os
import stat
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from .format import safe_text
from .models import (
    Item,
    ItemKind,
    ScanRule,
    ScanSession,
    ScanStatus,
)

_MAX_WARNINGS_PER_ITEM = 8
_MAX_SESSION_WARNINGS = 100


def _warn(warnings: list[str], message: str) -> None:
    if len(warnings) < _MAX_WARNINGS_PER_ITEM:
        warnings.append(safe_text(message))


def _warn_session(session: ScanSession, message: str) -> None:
    """Добавить предупреждение сессии, не раздувая ответ API без границ."""

    with session.lock:
        if len(session.warnings) < _MAX_SESSION_WARNINGS:
            session.warnings.append(safe_text(message))


def measure_path(
    path: Path,
    cancel_event: threading.Event | None = None,
    deadline: float | None = None,
    warnings: list[str] | None = None,
) -> tuple[int, int]:
    """Посчитать (занято на диске, видимый размер) в байтах.

    Симлинки не разыменовываются, жёсткие ссылки внутри одного пункта
    учитываются один раз. Ошибки доступа попадают в ``warnings``.
    """

    warnings = warnings if warnings is not None else []
    try:
        root_stat = path.lstat()
    except OSError as exc:
        _warn(warnings, f"Нет доступа: {path} ({exc.strerror})")
        return 0, 0

    if stat.S_ISLNK(root_stat.st_mode):
        _warn(warnings, f"Символическая ссылка пропущена: {path}")
        return 0, 0
    if stat.S_ISREG(root_stat.st_mode):
        return root_stat.st_blocks * 512, root_stat.st_size
    if not stat.S_ISDIR(root_stat.st_mode):
        return 0, 0

    disk_bytes = 0
    apparent_bytes = 0
    seen_hardlinks: set[tuple[int, int]] = set()
    stack = [path]
    visited = 0

    while stack:
        if cancel_event is not None and cancel_event.is_set():
            _warn(warnings, "Сканирование отменено")
            break
        if deadline is not None and time.monotonic() > deadline:
            _warn(warnings, "Превышен лимит времени сканирования")
            break

        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    visited += 1
                    if visited % 256 == 0:
                        if cancel_event is not None and cancel_event.is_set():
                            break
                        if deadline is not None and time.monotonic() > deadline:
                            break
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        _warn(warnings, f"Нет доступа: {entry.path} ({exc.strerror})")
                        continue

                    if stat.S_ISLNK(entry_stat.st_mode):
                        _warn(warnings, f"Символическая ссылка пропущена: {entry.path}")
                        continue
                    if stat.S_ISDIR(entry_stat.st_mode):
                        # Не заходим в чужие файловые системы: удаление
                        # каталога с точкой монтирования всё равно запрещено.
                        if os.path.ismount(entry.path):
                            _warn(
                                warnings,
                                f"Точка монтирования пропущена: {entry.path}",
                            )
                            continue
                        stack.append(Path(entry.path))
                        continue
                    if not stat.S_ISREG(entry_stat.st_mode):
                        continue

                    if entry_stat.st_nlink > 1:
                        key = (entry_stat.st_dev, entry_stat.st_ino)
                        if key in seen_hardlinks:
                            _warn(
                                warnings,
                                f"Жёсткая ссылка учтена один раз: {entry.path}",
                            )
                            continue
                        seen_hardlinks.add(key)

                    disk_bytes += entry_stat.st_blocks * 512
                    apparent_bytes += entry_stat.st_size
        except OSError as exc:
            _warn(warnings, f"Нет доступа: {current} ({exc.strerror})")

    return disk_bytes, apparent_bytes


class Scanner:
    """Обход правил в одном фоновом потоке."""

    def __init__(
        self,
        rules: tuple[ScanRule, ...],
        max_items: int = 5_000,
        max_seconds: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rules = tuple(rules)
        self.max_items = max_items
        self.max_seconds = max_seconds
        self._clock = clock

    def _old_enough(self, st: os.stat_result, min_age_days: int) -> bool:
        if min_age_days <= 0:
            return True
        return (time.time() - st.st_mtime) >= min_age_days * 86_400

    def _candidates(
        self,
        rule: ScanRule,
        deadline: float | None,
        session: ScanSession,
    ) -> Iterator[tuple[Path, Path]]:
        for root in rule.roots:
            if rule.selector == "children":
                # Не материализуем весь каталог: ~/.cache может содержать
                # десятки тысяч записей, а лимит пунктов всё равно небольшой.
                try:
                    for candidate in root.iterdir():
                        if session.cancel_event.is_set():
                            return
                        if deadline is not None and self._clock() > deadline:
                            return
                        try:
                            st = candidate.lstat()
                        except OSError:
                            continue
                        if stat.S_ISLNK(st.st_mode):
                            continue
                        if not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
                            continue
                        if stat.S_ISDIR(st.st_mode) and os.path.ismount(candidate):
                            _warn_session(
                                session,
                                f"Точка монтирования пропущена: {candidate}",
                            )
                            continue
                        if not self._old_enough(st, rule.min_age_days):
                            continue
                        yield candidate, root
                except OSError:
                    continue
            elif rule.selector == "glob":
                for pattern in rule.patterns:
                    try:
                        for candidate in root.glob(pattern):
                            if session.cancel_event.is_set():
                                return
                            if deadline is not None and self._clock() > deadline:
                                return
                            try:
                                st = candidate.lstat()
                            except OSError:
                                continue
                            if not stat.S_ISREG(st.st_mode):
                                continue
                            if not self._old_enough(st, rule.min_age_days):
                                continue
                            yield candidate, root
                    except OSError:
                        continue

    @staticmethod
    def _has_ancestor(paths: set[str], candidate: Path) -> bool:
        parent = candidate.parent
        while True:
            if str(parent) in paths:
                return True
            if parent == parent.parent:
                return False
            parent = parent.parent

    def scan(
        self,
        session: ScanSession,
        rule_ids: list[str] | None = None,
    ) -> None:
        """Заполнить сессию результатами. Вызывается в отдельном потоке."""

        selected = set(rule_ids) if rule_ids is not None else None
        rules = [
            rule
            for rule in self.rules
            if not rule.requires_privilege and (selected is None or rule.id in selected)
        ]

        deadline = self._clock() + self.max_seconds if self.max_seconds > 0 else None
        stop_reason: str | None = None

        with session.lock:
            session.status = ScanStatus.RUNNING
            session.progress.phase = "scanning"

        if session.cancel_event.is_set():
            with session.lock:
                session.status = ScanStatus.CANCELLED
                session.progress.phase = "cancelled"
                session.finished_at = time.time()
            return

        try:
            for rule in rules:
                if stop_reason is not None:
                    break
                for candidate, _root in self._candidates(rule, deadline, session):
                    if stop_reason is not None:
                        break
                    if session.cancel_event.is_set():
                        stop_reason = "cancelled"
                        break
                    if deadline is not None and self._clock() > deadline:
                        stop_reason = "timeout"
                        break

                    try:
                        st = candidate.lstat()
                    except OSError:
                        continue

                    with session.lock:
                        if str(candidate) in session.paths or self._has_ancestor(
                            session.paths, candidate
                        ):
                            continue
                        session.progress.current_path = str(candidate)

                    warnings: list[str] = []
                    disk_bytes, apparent_bytes = measure_path(
                        candidate,
                        cancel_event=session.cancel_event,
                        deadline=deadline,
                        warnings=warnings,
                    )

                    if session.cancel_event.is_set():
                        stop_reason = "cancelled"
                        break

                    # Абсолютный путь, а не relative_to(root): у правила
                    # может быть несколько корней, и одинаковые имена в них
                    # не должны давать один и тот же item_id.
                    item_id = hashlib.sha256(
                        f"{session.id}|{rule.id}|{candidate}".encode(
                            "utf-8", errors="surrogateescape"
                        )
                    ).hexdigest()[:16]
                    kind = (
                        ItemKind.DIRECTORY
                        if stat.S_ISDIR(st.st_mode)
                        else ItemKind.FILE
                    )
                    item = Item(
                        id=item_id,
                        rule_id=rule.id,
                        path=candidate,
                        kind=kind,
                        size_bytes=disk_bytes,
                        apparent_bytes=apparent_bytes,
                        mtime=st.st_mtime,
                        risk=rule.risk,
                        dev=st.st_dev,
                        ino=st.st_ino,
                        warnings=tuple(warnings),
                    )

                    with session.lock:
                        session.items[item.id] = item
                        session.paths.add(str(candidate))
                        session.progress.items_found = len(session.items)
                        session.progress.bytes_found += disk_bytes

                    if len(session.items) >= self.max_items:
                        stop_reason = "truncated"
                        break
                    if deadline is not None and self._clock() > deadline:
                        stop_reason = "timeout"
                        break
                # Отмена/дедлайн могли сработать внутри _candidates, когда
                # генератор завершился, не отдав ни одного кандидата.
                if stop_reason is None:
                    if session.cancel_event.is_set():
                        stop_reason = "cancelled"
                    elif deadline is not None and self._clock() > deadline:
                        stop_reason = "timeout"
        except Exception as exc:  # Сессия должна завершиться предсказуемо.
            with session.lock:
                session.status = ScanStatus.FAILED
                session.warnings.append(f"Сканирование не удалось: {exc}")
                session.progress.phase = "failed"
                session.finished_at = time.time()
            return

        with session.lock:
            if stop_reason == "cancelled":
                session.status = ScanStatus.CANCELLED
                session.progress.phase = "cancelled"
            else:
                session.status = ScanStatus.DONE
                session.progress.phase = "done"
            if stop_reason in {"timeout", "truncated"}:
                session.progress.truncated = True
                if len(session.warnings) < _MAX_SESSION_WARNINGS:
                    session.warnings.append(
                        "Результаты неполные: достигнут лимит времени "
                        "или количества пунктов"
                    )
            session.progress.current_path = ""
            session.finished_at = time.time()
