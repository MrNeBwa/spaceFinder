"""Корзина по спецификации freedesktop.org.

Файлы перемещаются в ``$XDG_DATA_HOME/Trash/files``, рядом создаётся
``.trashinfo`` с исходным путём и датой. Операция обратима вручную.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_from_bytes

from . import fsops
from .path_policy import PathPolicy, ProtectedPathError

logger = logging.getLogger(__name__)

MoveFn = Callable[[Path, Path, PathPolicy], None]


class TrashService:
    def __init__(
        self,
        data_home: Path,
        policy: PathPolicy,
        move: MoveFn | None = None,
    ) -> None:
        self.trash_dir = Path(data_home) / "Trash"
        self.files_dir = self.trash_dir / "files"
        self.info_dir = self.trash_dir / "info"
        self.policy = policy
        self._move = move or fsops.safe_move
        # Имена в files/ и info/ резервируются под замком, иначе два
        # параллельных удаления могут выбрать одно и то же имя.
        self._lock = threading.Lock()

    def _check_layout(self) -> None:
        for directory in (self.trash_dir, self.files_dir, self.info_dir):
            # Симлинк на чужой каталог превратил бы корзину в способ
            # писать/удалять данные вне $XDG_DATA_HOME.
            if directory.is_symlink():
                raise ProtectedPathError(
                    "protected_path",
                    f"Каталог корзины не должен быть символической ссылкой: {directory}",
                )

    def _ensure_dirs(self) -> None:
        self._check_layout()
        for directory in (self.trash_dir, self.files_dir, self.info_dir):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                if not directory.is_symlink():
                    directory.chmod(0o700)
            except OSError:
                # Права уже могли быть выставлены администратором.
                pass

    def contains(self, path: Path) -> bool:
        """Находится ли объект уже внутри ``Trash/files``."""

        candidate = Path(os.path.abspath(path))
        try:
            candidate.relative_to(self.files_dir)
            return True
        except ValueError:
            pass
        try:
            resolved = Path(os.path.realpath(candidate))
            files = Path(os.path.realpath(self.files_dir))
            resolved.relative_to(files)
            return True
        except (OSError, ValueError):
            return False

    def _unique_name(self, name: str) -> str:
        candidate = name
        counter = 1
        while (self.files_dir / candidate).exists() or (
            self.info_dir / f"{candidate}.trashinfo"
        ).exists():
            candidate = f"{name}.{counter}"
            counter += 1
        return candidate

    def _write_info(self, info_path: Path, original: Path) -> None:
        deletion_date = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        # os.fsencode сохраняет исходные байты имени даже для не-UTF-8
        # файлов; quote_from_bytes не падает на surrogateescape.
        encoded_path = quote_from_bytes(os.fsencode(str(original)), safe="/")
        payload = f"[Trash Info]\nPath={encoded_path}\nDeletionDate={deletion_date}\n"
        fd = os.open(info_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            with contextlib.suppress(OSError):
                info_path.unlink()
            raise

    def _cleanup_partial(self, destination: Path) -> None:
        """Убрать недописанную копию после неудачного ``shutil.move``."""

        try:
            if destination.is_symlink() or destination.is_file():
                destination.unlink()
            elif destination.is_dir():
                fsops.safe_rmtree(destination, self.policy)
        except (OSError, ProtectedPathError):
            # Частичная копия не должна маскировать исходную ошибку.
            logger.warning(
                "Не удалось убрать частичную копию %s", destination, exc_info=True
            )

    def trash(self, path: Path) -> Path:
        """Переместить объект в корзину и вернуть новый путь."""

        original = Path(os.path.abspath(path))
        source = self.policy.ensure_deletable(path)
        if not source.is_symlink() and fsops.has_nested_mount(source):
            raise ProtectedPathError(
                "mount_point",
                f"Нельзя переместить каталог с точкой монтирования: {source}",
            )

        with self._lock:
            self._ensure_dirs()
            while True:
                name = self._unique_name(source.name)
                info_path = self.info_dir / f"{name}.trashinfo"
                try:
                    self._write_info(info_path, original)
                except FileExistsError:
                    # Имя занял другой процесс (например, файловый менеджер).
                    continue
                break
            destination = self.files_dir / name

        try:
            self._move(source, destination, self.policy)
        except Exception:
            # Исходник не перемещён — убираем осиротевший .trashinfo
            # и возможную недописанную копию на другой файловой системе.
            with contextlib.suppress(OSError):
                info_path.unlink()
            self._cleanup_partial(destination)
            raise
        return destination

    def expunge(self, path: Path) -> None:
        """Удалить объект из корзины вместе с его ``.trashinfo``.

        Используется правилом «Корзина»: обычное перемещение внутри
        корзины не освободило бы место и оставило бы метаданные.
        """

        self._check_layout()
        target = self.policy.ensure_deletable(path)
        files = Path(os.path.realpath(self.files_dir))
        resolved = Path(os.path.realpath(target))
        try:
            relative = resolved.relative_to(files)
        except ValueError as exc:
            raise ValueError(f"Объект не находится в корзине: {target}") from exc
        if len(relative.parts) != 1:
            raise ValueError(
                f"Можно очищать только элементы верхнего уровня корзины: {target}"
            )

        info_path = self.info_dir / f"{target.name}.trashinfo"
        if target.is_dir() and not target.is_symlink():
            fsops.safe_rmtree(target, self.policy)
        else:
            fsops.safe_unlink(target, self.policy)
        # Метаданные удаляем после объекта: если процесс упадёт между
        # операциями, останется осиротевший .trashinfo, но не запись
        # об успешной очистке при неудалённом объекте.
        try:
            info_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            # Объект уже удалён; осиротевшие метаданные не должны
            # превращать успешную очистку в ошибку.
            logger.warning("Не удалось удалить %s", info_path, exc_info=True)
