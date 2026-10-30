"""Форматирование значений для API и UI."""

from __future__ import annotations

from pathlib import Path

_UNITS = ("Б", "КБ", "МБ", "ГБ", "ТБ", "ПБ")


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
        return str(path)
    if str(relative) == ".":
        return "~"
    return f"~/{relative}"
