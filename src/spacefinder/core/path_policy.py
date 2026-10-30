"""Политика путей: что и где приложению разрешено удалять."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path


class ProtectedPathError(ValueError):
    """Путь не прошёл проверку безопасности."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _resolve(path: Path) -> Path:
    return Path(os.path.realpath(os.path.expanduser(str(path))))


def _within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


class PathPolicy:
    """Разрешает удаление только внутри известных корней.

    Проверки применяются к родительскому каталогу: сам листовой объект
    может быть символьной ссылкой, и тогда удаляется только ссылка, а не
    её цель.
    """

    def __init__(
        self,
        allowed_roots: Iterable[Path],
        protected: Iterable[Path],
        test_root: Path | None = None,
        protected_subtrees: Iterable[Path] = (),
    ) -> None:
        self.allowed_roots = tuple(
            dict.fromkeys(_resolve(Path(p)) for p in allowed_roots)
        )
        self.protected = tuple(dict.fromkeys(_resolve(Path(p)) for p in protected))
        self.protected_subtrees = tuple(
            dict.fromkeys(_resolve(Path(p)) for p in protected_subtrees)
        )
        self.test_root = _resolve(test_root) if test_root is not None else None

        # Симлинк вроде ~/.cache -> / не должен превращать весь диск в allowlist.
        for root in self.allowed_roots:
            if root == Path("/") or any(
                root == protected_path or _within(protected_path, root)
                for protected_path in self.protected
            ):
                raise ValueError(
                    f"Небезопасный корень правила (совпадает с защищённым путём): {root}"
                )
            if any(
                root == subtree or _within(root, subtree)
                for subtree in self.protected_subtrees
            ):
                raise ValueError(
                    f"Небезопасный корень правила (внутри защищённого каталога): {root}"
                )

    def ensure_deletable(self, path: Path) -> Path:
        """Вернуть нормализованный путь или бросить :class:`ProtectedPathError`."""

        raw = Path(path)
        if not raw.is_absolute():
            raise ProtectedPathError(
                "invalid_path", f"Путь должен быть абсолютным: {raw}"
            )
        if "\x00" in str(raw):
            raise ProtectedPathError("invalid_path", "Путь содержит NUL-байт")
        if raw.name in {"", ".", ".."}:
            raise ProtectedPathError("invalid_path", f"Некорректное имя пути: {raw}")

        # Родитель разыменовываем: нельзя удалять файл через symlink-каталог,
        # ведущий за пределы разрешённых корней.
        parent = Path(os.path.realpath(raw.parent))
        leaf = parent / raw.name

        if self.test_root is not None and not _within(leaf, self.test_root):
            raise ProtectedPathError(
                "protected_path", f"Тестовый режим: путь вне песочницы: {leaf}"
            )

        if any(leaf == root for root in self.allowed_roots):
            raise ProtectedPathError(
                "protected_path", f"Нельзя удалить корень правила целиком: {leaf}"
            )
        if not any(_within(leaf, root) for root in self.allowed_roots):
            raise ProtectedPathError(
                "outside_allowed_roots", f"Путь вне разрешённых каталогов: {leaf}"
            )

        if any(leaf == p for p in self.protected):
            raise ProtectedPathError("protected_path", f"Защищённый путь: {leaf}")
        if any(_within(leaf, p) for p in self.protected_subtrees):
            raise ProtectedPathError("protected_path", f"Защищённый каталог: {leaf}")

        try:
            if not leaf.is_symlink() and os.path.ismount(leaf):
                raise ProtectedPathError(
                    "protected_path", f"Нельзя удалять точку монтирования: {leaf}"
                )
        except OSError:
            # Если lstat не удался, операция всё равно упадёт позже с понятной ошибкой.
            pass

        return leaf
