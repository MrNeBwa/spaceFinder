"""Сервис удаления: корзина, безвозвратное удаление и dry-run."""

from __future__ import annotations

import logging
import stat
from datetime import UTC, datetime

from . import fsops
from .history import HistoryLog
from .models import (
    DeleteMode,
    DeleteResult,
    DeleteStatus,
    Item,
    ItemKind,
    ScanSession,
    ScanStatus,
)
from .path_policy import PathPolicy, ProtectedPathError
from .trash import TrashService

logger = logging.getLogger(__name__)


class DeleteValidationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DeleteService:
    def __init__(
        self,
        trash: TrashService,
        policy: PathPolicy,
        history: HistoryLog,
        *,
        allow_permanent: bool = False,
        dry_run: bool = False,
    ) -> None:
        self.trash = trash
        self.policy = policy
        self.history = history
        self.allow_permanent = allow_permanent
        self.dry_run = dry_run

    def _check_overlaps(self, items: list[Item]) -> None:
        """Отклонить выборку, где один пункт вложен в другой.

        Проверка идёт по предкам каждого пути: O(n * глубина), а не O(n²).
        """

        paths = {item.path for item in items}
        if len(paths) != len(items):
            raise DeleteValidationError(
                "conflict",
                "Один и тот же путь выбран несколько раз",
            )
        for item in items:
            parent = item.path.parent
            while parent != parent.parent:
                if parent in paths:
                    raise DeleteValidationError(
                        "conflict",
                        "Нельзя одновременно удалить каталог и объект внутри него",
                    )
                parent = parent.parent

    def _audit(
        self,
        session: ScanSession,
        item: Item,
        mode: DeleteMode,
        result: DeleteResult,
    ) -> None:
        record = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "scan_id": session.id,
            "item_id": item.id,
            "path": str(item.path),
            "mode": mode.value,
            "status": result.status.value,
            "size_bytes": result.freed_bytes,
            "error": result.error_code,
        }
        try:
            self.history.append(record)
        except Exception:
            # Аудит важен, но его сбой не должен превращать уже выполненное
            # удаление в ошибку и обрывать обработку остальных пунктов.
            logger.exception("Не удалось записать аудит для %s", item.path)

    def _delete_one(
        self,
        item: Item,
        mode: DeleteMode,
    ) -> DeleteResult:
        try:
            st = item.path.lstat()
        except FileNotFoundError:
            return DeleteResult(
                item_id=item.id,
                status=DeleteStatus.SKIPPED,
                freed_bytes=0,
                message="Объект уже отсутствует",
            )
        except OSError as exc:
            return DeleteResult(
                item_id=item.id,
                status=DeleteStatus.FAILED,
                freed_bytes=0,
                error_code=f"errno_{exc.errno}",
                message=exc.strerror or str(exc),
            )

        actual_kind = ItemKind.DIRECTORY if stat.S_ISDIR(st.st_mode) else ItemKind.FILE
        if actual_kind != item.kind:
            return DeleteResult(
                item_id=item.id,
                status=DeleteStatus.FAILED,
                freed_bytes=0,
                error_code="changed",
                message="Тип объекта изменился после сканирования",
            )
        if (st.st_dev, st.st_ino) != (item.dev, item.ino):
            return DeleteResult(
                item_id=item.id,
                status=DeleteStatus.FAILED,
                freed_bytes=0,
                error_code="changed",
                message="Объект заменён после сканирования",
            )

        if mode is DeleteMode.DRY_RUN:
            return DeleteResult(
                item_id=item.id,
                status=DeleteStatus.SIMULATED,
                freed_bytes=item.size_bytes,
                message="Режим dry-run: объект не изменён",
            )

        try:
            if mode is DeleteMode.TRASH:
                # Объект уже в корзине: «перемещение в корзину» не освободит
                # место, поэтому очистка корзины удаляет его окончательно.
                if self.trash.contains(item.path):
                    self.trash.expunge(item.path)
                    return DeleteResult(
                        item_id=item.id,
                        status=DeleteStatus.DELETED,
                        freed_bytes=item.size_bytes,
                        message="Корзина очищена",
                    )

                destination = self.trash.trash(item.path)
                return DeleteResult(
                    item_id=item.id,
                    status=DeleteStatus.TRASHED,
                    # Перемещение в корзину не освобождает место до её очистки.
                    freed_bytes=0,
                    target=str(destination),
                )

            if mode is DeleteMode.PERMANENT:
                if self.trash.contains(item.path):
                    # Безвозвратное удаление пункта корзины должно убрать
                    # и его .trashinfo, иначе в корзине останутся «призраки».
                    self.trash.expunge(item.path)
                elif item.kind is ItemKind.DIRECTORY:
                    fsops.safe_rmtree(item.path, self.policy)
                else:
                    fsops.safe_unlink(item.path, self.policy)
                return DeleteResult(
                    item_id=item.id,
                    status=DeleteStatus.DELETED,
                    freed_bytes=item.size_bytes,
                )
        except ProtectedPathError as exc:
            return DeleteResult(
                item_id=item.id,
                status=DeleteStatus.FAILED,
                freed_bytes=0,
                error_code=exc.code,
                message=exc.message,
            )
        except OSError as exc:
            return DeleteResult(
                item_id=item.id,
                status=DeleteStatus.FAILED,
                freed_bytes=0,
                error_code=f"errno_{exc.errno}",
                message=exc.strerror or str(exc),
            )
        except Exception as exc:
            logger.exception("Ошибка удаления %s", item.path)
            return DeleteResult(
                item_id=item.id,
                status=DeleteStatus.FAILED,
                freed_bytes=0,
                error_code="internal_error",
                message=str(exc),
            )

        return DeleteResult(
            item_id=item.id,
            status=DeleteStatus.FAILED,
            freed_bytes=0,
            error_code="invalid_mode",
            message=f"Неизвестный режим: {mode}",
        )

    def delete(
        self,
        session: ScanSession,
        item_ids: list[str],
        mode: DeleteMode | str,
        confirmed: bool,
    ) -> list[DeleteResult]:
        if not confirmed:
            raise DeleteValidationError(
                "confirmation_required", "Удаление требует явного подтверждения"
            )
        try:
            requested_mode = DeleteMode(mode)
        except ValueError as exc:
            raise DeleteValidationError(
                "invalid_request", f"Неизвестный режим удаления: {mode}"
            ) from exc

        if requested_mode is DeleteMode.PERMANENT and not self.allow_permanent:
            raise DeleteValidationError(
                "permanent_disabled",
                "Безвозвратное удаление отключено. Запустите с --allow-permanent",
            )

        effective_mode = DeleteMode.DRY_RUN if self.dry_run else requested_mode
        unique_ids = list(dict.fromkeys(item_ids))
        if not unique_ids:
            raise DeleteValidationError("invalid_request", "Не выбран ни один пункт")

        # Файловые операции могут занимать минуты. Держим session.lock
        # только на чтение/запись состояния, иначе GET /scans и отмена
        # сканирования ждут всё удаление.
        if not session.delete_lock.acquire(blocking=False):
            raise DeleteValidationError(
                "delete_in_progress", "Удаление уже выполняется"
            )
        try:
            with session.lock:
                if session.status not in {ScanStatus.DONE, ScanStatus.CANCELLED}:
                    raise DeleteValidationError(
                        "scan_not_ready", "Сканирование ещё не завершено"
                    )

                missing = [
                    item_id for item_id in unique_ids if item_id not in session.items
                ]
                if missing:
                    raise DeleteValidationError(
                        "item_not_found", f"Пункт не найден: {missing[0]}"
                    )
                already = [
                    item_id for item_id in unique_ids if item_id in session.deleted_ids
                ]
                if already:
                    raise DeleteValidationError(
                        "conflict", f"Пункт уже удалён: {already[0]}"
                    )

                items = [session.items[item_id] for item_id in unique_ids]
                self._check_overlaps(items)

            results: list[DeleteResult] = []
            for item in items:
                result = self._delete_one(item, effective_mode)
                results.append(result)
                if result.status in {DeleteStatus.TRASHED, DeleteStatus.DELETED}:
                    with session.lock:
                        session.deleted_ids.add(item.id)
                self._audit(session, item, effective_mode, result)
            return results
        finally:
            session.delete_lock.release()
