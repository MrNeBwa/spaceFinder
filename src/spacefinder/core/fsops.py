"""Единственная точка деструктивных операций с файловой системой.

Все функции проверяют путь через :class:`PathPolicy`. Тесты подменяют
именно эти функции, чтобы гарантировать отсутствие реальных удалений.
"""

from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path

from .path_policy import PathPolicy


def safe_move(src: Path, dst: Path, policy: PathPolicy) -> None:
    """Переместить объект, не выходя за пределы разрешённых путей."""

    source = policy.ensure_deletable(src)
    destination = Path(dst)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(source, destination)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        # Разные файловые системы: копирование с последующим удалением источника.
        shutil.move(str(source), str(destination))


def has_nested_mount(path: Path) -> bool:
    """Есть ли внутри каталога точка монтирования.

    ``shutil.rmtree`` иначе зайдёт в неё и удалит данные другого раздела.
    """

    if path.is_symlink() or not path.is_dir():
        return False
    for root, directories, _ in os.walk(path, followlinks=False):
        for name in directories:
            if os.path.ismount(Path(root) / name):
                return True
    return False


def safe_rmtree(path: Path, policy: PathPolicy) -> None:
    """Рекурсивно удалить каталог (без перехода по симлинкам)."""

    target = policy.ensure_deletable(path)
    if target.is_symlink() or not target.is_dir():
        raise NotADirectoryError(f"Не каталог: {target}")
    if has_nested_mount(target):
        raise OSError(errno.EBUSY, f"Внутри каталога есть точка монтирования: {target}")
    shutil.rmtree(target)


def safe_unlink(path: Path, policy: PathPolicy) -> None:
    """Удалить файл или символьную ссылку."""

    target = policy.ensure_deletable(path)
    if target.is_dir() and not target.is_symlink():
        raise IsADirectoryError(f"Это каталог, используйте safe_rmtree: {target}")
    target.unlink()
