"""Реестр правил сканирования.

Правила могут пересекаться по путям (например, обычный кэш и «крупные
кэши»): сканер дедуплицирует пункты по абсолютному пути. Системные кэши
и установленные пакеты только показываются пользователю: приложение не
удаляет их без root-прав.
"""

from __future__ import annotations

from pathlib import Path

from .models import RiskLevel, RuleCategory, ScanRule

_MB = 1024 * 1024


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
            id="large_caches",
            title="Крупные кэши",
            description=(
                "Элементы ~/.cache от 500 МБ, включая свежие. После удаления "
                "приложение создаст кэш заново."
            ),
            risk=RiskLevel.MEDIUM,
            default_selected=False,
            roots=(cache,),
            selector="children",
            min_size_bytes=500 * _MB,
        ),
        ScanRule(
            id="app_data",
            title="Приложения и их данные",
            description=(
                "Игры Steam, экземпляры PrismLauncher и данные Flatpak "
                "размером от 500 МБ. Место освобождается после очистки корзины."
            ),
            risk=RiskLevel.HIGH,
            default_selected=False,
            roots=(
                home / ".local" / "share" / "Steam" / "steamapps" / "common",
                home / ".local" / "share" / "PrismLauncher" / "instances",
                home / ".var" / "app",
            ),
            selector="children",
            min_size_bytes=500 * _MB,
            category=RuleCategory.APPS,
        ),
        ScanRule(
            id="dev_modules",
            title="Модули разработки",
            description=(
                "node_modules, .venv, target, build и другие каталоги "
                "зависимостей в ~/Projects размером от 200 МБ."
            ),
            risk=RiskLevel.MEDIUM,
            default_selected=False,
            roots=(home / "Projects",),
            selector="module_dirs",
            patterns=(
                "node_modules",
                ".venv",
                "venv",
                ".tox",
                "target",
                "build",
                ".next",
                ".nuxt",
                ".gradle",
            ),
            min_size_bytes=200 * _MB,
            category=RuleCategory.APPS,
        ),
        ScanRule(
            id="gradle_cache",
            title="Кэш Gradle",
            description="Каталоги ~/.gradle от 500 МБ: кэши, JDK и данные daemon.",
            risk=RiskLevel.LOW,
            default_selected=False,
            roots=(home / ".gradle",),
            selector="children",
            min_size_bytes=500 * _MB,
            category=RuleCategory.APPS,
        ),
        ScanRule(
            id="nuget_cache",
            title="Кэш NuGet",
            description="Каталоги ~/.nuget от 200 МБ.",
            risk=RiskLevel.LOW,
            default_selected=False,
            roots=(home / ".nuget",),
            selector="children",
            min_size_bytes=200 * _MB,
            category=RuleCategory.APPS,
        ),
        ScanRule(
            id="go_modules",
            title="Модули Go",
            description="Каталоги ~/go/pkg от 200 МБ: скачанные модули и суммы.",
            risk=RiskLevel.LOW,
            default_selected=False,
            roots=(home / "go" / "pkg",),
            selector="children",
            min_size_bytes=200 * _MB,
            category=RuleCategory.APPS,
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
            category=RuleCategory.SYSTEM,
            info_commands=(
                "sudo pacman -Sc",
                "sudo journalctl --vacuum-size=200M",
            ),
        ),
        ScanRule(
            id="system_apps",
            title="Установленные приложения",
            description=(
                "Самые большие пакеты pacman, Flatpak и образы Docker. "
                "Удаление выполняется командами вручную."
            ),
            risk=RiskLevel.HIGH,
            default_selected=False,
            roots=(),
            selector="none",
            requires_privilege=True,
            category=RuleCategory.SYSTEM,
            info_commands=(
                "LC_ALL=C pacman -Qi | awk -F': ' "
                "'/^Name/{n=$2} /^Installed Size/{split($2,a,\" \"); "
                "v=a[1]; u=a[2]; m=1; if(u==\"KiB\")m=1024; "
                "else if(u==\"MiB\")m=1048576; else if(u==\"GiB\")m=1073741824; "
                "printf \"%.0f\\t%s\\n\", v*m, n}' "
                "| sort -rn | head -20 | LC_ALL=C numfmt --to=iec --field=1",
                "LC_ALL=C flatpak list --app --columns=application,size",
                "docker system df",
                "sudo pacman -Rns <package>",
                "flatpak uninstall <app-id>",
                "docker system prune -a",
            ),
        ),
    )


def rules_by_id(rules: tuple[ScanRule, ...]) -> dict[str, ScanRule]:
    return {rule.id: rule for rule in rules}
