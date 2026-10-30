"""Реестр правил сканирования.

Правила намеренно не пересекаются по путям. Системные кэши только
показываются пользователю: приложение не удаляет их без root-прав.
"""

from __future__ import annotations

from pathlib import Path

from .models import RiskLevel, ScanRule


def default_rules(home: Path, data_home: Path) -> tuple[ScanRule, ...]:
    cache = home / ".cache"
    return (
        ScanRule(
            id="user_cache",
            title="Кэш приложений",
            description="Содержимое ~/.cache старше 30 дней. Свежие кэши не трогаем.",
            risk=RiskLevel.LOW,
            default_selected=True,
            roots=(cache,),
            selector="children",
            min_age_days=30,
        ),
        ScanRule(
            id="npm_cache",
            title="Кэш npm",
            description="Содержимое ~/.npm/_cacache старше 30 дней.",
            risk=RiskLevel.LOW,
            default_selected=True,
            roots=(home / ".npm" / "_cacache",),
            selector="children",
            min_age_days=30,
        ),
        ScanRule(
            id="pnpm_cache",
            title="Хранилище pnpm",
            description="Пакеты в ~/.local/share/pnpm/store.",
            risk=RiskLevel.MEDIUM,
            default_selected=False,
            roots=(home / ".local" / "share" / "pnpm" / "store",),
            selector="children",
            min_age_days=30,
        ),
        ScanRule(
            id="bun_cache",
            title="Кэш bun",
            description="Содержимое ~/.bun/install/cache.",
            risk=RiskLevel.LOW,
            default_selected=False,
            roots=(home / ".bun" / "install" / "cache",),
            selector="children",
            min_age_days=30,
        ),
        ScanRule(
            id="cargo_cache",
            title="Кэш cargo",
            description="Скачанные крейты и git-базы cargo.",
            risk=RiskLevel.LOW,
            default_selected=False,
            roots=(
                home / ".cargo" / "registry" / "cache",
                home / ".cargo" / "git" / "db",
            ),
            selector="children",
            min_age_days=30,
        ),
        ScanRule(
            id="trash",
            title="Корзина",
            description="Файлы в корзине. После очистки восстановить их нельзя.",
            risk=RiskLevel.MEDIUM,
            default_selected=False,
            roots=(data_home / "Trash" / "files",),
            selector="children",
        ),
        ScanRule(
            id="downloads_installers",
            title="Старые установщики",
            description="ISO, образы и пакеты в ~/Downloads старше 30 дней.",
            risk=RiskLevel.HIGH,
            default_selected=False,
            roots=(home / "Downloads",),
            selector="glob",
            patterns=("*.iso", "*.img", "*.AppImage", "*.deb", "*.rpm"),
            min_age_days=30,
        ),
        ScanRule(
            id="system_info",
            title="Системные кэши",
            description="Очищаются вручную командами ниже: приложению нужны root-права.",
            risk=RiskLevel.HIGH,
            default_selected=False,
            roots=(),
            selector="none",
            requires_privilege=True,
            info_commands=(
                "sudo pacman -Sc",
                "sudo journalctl --vacuum-size=200M",
            ),
        ),
    )


def rules_by_id(rules: tuple[ScanRule, ...]) -> dict[str, ScanRule]:
    return {rule.id: rule for rule in rules}
