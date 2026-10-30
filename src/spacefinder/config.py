"""Конфигурация приложения: значения по умолчанию, окружение, XDG-пути."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} должно быть целым числом, получено: {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} должно быть числом, получено: {raw!r}") from exc


def xdg_data_home(home: Path | None = None) -> Path:
    raw = os.environ.get("XDG_DATA_HOME")
    if raw and Path(raw).is_absolute():
        return Path(raw)
    return (home or Path.home()) / ".local" / "share"


def xdg_state_home(home: Path | None = None) -> Path:
    raw = os.environ.get("XDG_STATE_HOME")
    if raw and Path(raw).is_absolute():
        return Path(raw)
    return (home or Path.home()) / ".local" / "state"


@dataclass(slots=True)
class AppConfig:
    """Настройки одного запуска приложения.

    Все пути абсолютные. В тестовом режиме ``test_mode=True`` и
    ``test_root`` ограничивают любые файловые операции песочницей.
    """

    host: str = "127.0.0.1"
    port: int = 8765
    dry_run: bool = False
    allow_permanent: bool = False
    max_items: int = 5_000
    scan_timeout: float = 120.0
    session_ttl: float = 900.0
    test_mode: bool = False
    test_root: Path | None = None
    home: Path = field(default_factory=Path.home)
    data_home: Path = field(default_factory=lambda: xdg_data_home())
    state_home: Path = field(default_factory=lambda: xdg_state_home())

    def __post_init__(self) -> None:
        if not 1 <= self.port <= 65_535:
            raise ValueError(f"Порт должен быть в диапазоне 1..65535: {self.port}")
        if self.host not in {"127.0.0.1", "localhost"}:
            raise ValueError(
                "Разрешён только локальный host: 127.0.0.1 или localhost"
            )
        if self.max_items < 1:
            raise ValueError("SPACEFINDER_MAX_ITEMS должен быть больше нуля")
        if self.max_items > 100_000:
            raise ValueError("SPACEFINDER_MAX_ITEMS не должен превышать 100000")
        if not math.isfinite(self.scan_timeout) or self.scan_timeout < 0:
            raise ValueError(
                "SPACEFINDER_SCAN_TIMEOUT должен быть неотрицательным числом"
            )
        if not math.isfinite(self.session_ttl) or self.session_ttl <= 0:
            raise ValueError("session_ttl должен быть положительным числом")

        # Приводим пути к абсолютным один раз, чтобы display_path,
        # disk_usage и PathPolicy работали с одинаковыми значениями.
        self.home = Path(self.home).expanduser().resolve()
        self.data_home = Path(self.data_home).expanduser().resolve()
        self.state_home = Path(self.state_home).expanduser().resolve()
        if self.test_root is not None:
            self.test_root = Path(self.test_root).expanduser().resolve()

    @property
    def allowed_hosts(self) -> frozenset[str]:
        hosts = {
            f"127.0.0.1:{self.port}",
            f"localhost:{self.port}",
        }
        if self.port in {80, 443}:
            hosts.update({"127.0.0.1", "localhost"})
        if self.test_mode:
            hosts.add("testserver")
        return frozenset(hosts)

    @property
    def allowed_origins(self) -> frozenset[str]:
        return frozenset(
            f"http://{host}" for host in self.allowed_hosts if host != "testserver"
        )

    @classmethod
    def from_env(cls, **overrides: object) -> AppConfig:
        """Собрать конфигурацию из переменных окружения и явных переопределений."""

        home_override = os.environ.get("SPACEFINDER_HOME")
        home = (
            Path(home_override).expanduser().resolve()
            if home_override
            else Path.home().resolve()
        )

        # Если домашний каталог подменён (демо/тесты), XDG-пути тоже держим
        # внутри него, чтобы не задеть настоящую корзину пользователя.
        if home_override:
            data_home = home / ".local" / "share"
            state_home = home / ".local" / "state"
        else:
            data_home = xdg_data_home(home)
            state_home = xdg_state_home(home)

        values: dict[str, object] = {
            "host": os.environ.get("SPACEFINDER_HOST", "127.0.0.1"),
            "port": _env_int("SPACEFINDER_PORT", 8765),
            "dry_run": _env_bool("SPACEFINDER_DRY_RUN"),
            "allow_permanent": _env_bool("SPACEFINDER_ALLOW_PERMANENT"),
            "max_items": _env_int("SPACEFINDER_MAX_ITEMS", 5000),
            "scan_timeout": _env_float("SPACEFINDER_SCAN_TIMEOUT", 120.0),
            "test_mode": _env_bool("SPACEFINDER_TEST_MODE"),
            "home": home,
            "data_home": data_home,
            "state_home": state_home,
        }
        test_root = os.environ.get("SPACEFINDER_TEST_ROOT")
        if test_root:
            values["test_root"] = Path(test_root).expanduser().resolve()

        values.update(overrides)
        return cls(**values)  # type: ignore[arg-type]
