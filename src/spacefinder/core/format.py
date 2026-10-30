"""Форматирование значений для API и UI."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_UNITS = ("Б", "КБ", "МБ", "ГБ", "ТБ", "ПБ")
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def safe_text(value: str) -> str:
    """Заменить одиночные surrogate-символы на U+FFFD.

    Имена файлов в Linux не обязаны быть UTF-8. ``os.scandir`` возвращает
    такие имена с surrogateescape, а Pydantic/FastAPI отказываются
    сериализовать их в JSON. Для UI и API показываем безопасную замену.
    """

    return _SURROGATE_RE.sub("\ufffd", value)


def safe_json(value: Any) -> Any:
    """Рекурсивно обезопасить строки перед JSON-ответом."""

    if isinstance(value, str):
        return safe_text(value)
    if isinstance(value, list):
        return [safe_json(item) for item in value]
    if isinstance(value, tuple):
        return [safe_json(item) for item in value]
    if isinstance(value, dict):
        return {
            safe_text(key) if isinstance(key, str) else key: safe_json(item)
            for key, item in value.items()
        }
    return value


def human_bytes(value: int) -> str:
    if value < 0:
        return "—"
    if value < 1024:
        return f"{value} Б"
    number = float(value)
    unit = _UNITS[0]
    for candidate in _UNITS[1:]:
        number /= 1024.0
        unit = candidate
        if number < 1024.0:
            break
    text = f"{number:.1f}".replace(".", ",")
    return f"{text} {unit}"


def display_path(path: Path, home: Path) -> str:
    """Сократить домашний каталог до ``~`` для отображения."""

    try:
        relative = Path(path).relative_to(home)
    except ValueError:
        return safe_text(str(path))
    if str(relative) == ".":
        return "~"
    return safe_text(f"~/{relative}")
