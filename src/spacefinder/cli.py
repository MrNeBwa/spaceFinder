"""Командная строка spaceFinder."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import webbrowser
from dataclasses import replace
from typing import Any

from . import __version__
from .config import AppConfig
from .core.format import display_path, human_bytes, safe_text
from .core.models import Progress, ScanSession, ScanStatus


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spacefinder",
        description="Локальный помощник очистки диска",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="запустить веб-интерфейс")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--dry-run", action="store_true")
    serve.add_argument("--allow-permanent", action="store_true")
    serve.add_argument("--no-browser", action="store_true")
    serve.add_argument("--log-level", default="info")

    scan = subparsers.add_parser("scan", help="сканировать без запуска сервера")
    scan.add_argument("--rule", action="append", dest="rules")
    scan.add_argument("--json", action="store_true", help="вывод в JSON")
    return parser


def _serve(args: argparse.Namespace) -> int:
    try:
        config = AppConfig.from_env()
        overrides: dict[str, Any] = {}
        if args.host is not None:
            overrides["host"] = args.host
        if args.port is not None:
            overrides["port"] = args.port
        if args.dry_run:
            overrides["dry_run"] = True
        if args.allow_permanent:
            overrides["allow_permanent"] = True
        config = replace(config, **overrides)
    except ValueError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2
    if config.host not in {"127.0.0.1", "localhost"}:
        print(
            "Разрешён только локальный host: 127.0.0.1 или localhost",
            file=sys.stderr,
        )
        return 2

    from .app import create_app

    try:
        app = create_app(config)
    except ValueError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2
    url = f"http://{config.host}:{config.port}/"
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=[url]).start()

    try:
        import uvicorn
    except ImportError:
        print(
            "uvicorn не установлен. Выполните: pip install -e .",
            file=sys.stderr,
        )
        return 1

    print(f"spaceFinder: {url}")
    if config.dry_run:
        print("Режим dry-run: файлы не изменяются.")
    uvicorn.run(app, host=config.host, port=config.port, log_level=args.log_level)
    return 0


def _scan(args: argparse.Namespace) -> int:
    from .app import create_app

    try:
        config = AppConfig.from_env()
        app = create_app(config)
    except ValueError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2
    scanner = app.state.scanner
    session = ScanSession(
        id="cli",
        created_at=time.time(),
        status=ScanStatus.QUEUED,
        progress=Progress(),
    )
    try:
        rules = app.state.rules_by_id
        if args.rules:
            unknown = [rule_id for rule_id in args.rules if rule_id not in rules]
            if unknown:
                print(f"Неизвестное правило: {unknown[0]}", file=sys.stderr)
                return 2
            privileged = [
                rule_id
                for rule_id in args.rules
                if rules[rule_id].requires_privilege
            ]
            if privileged:
                print(
                    f"Правило требует root-прав: {privileged[0]}",
                    file=sys.stderr,
                )
                return 2
        scanner.scan(session, args.rules)
    except Exception as exc:  # CLI должен вернуть код, а не traceback
        print(f"Ошибка сканирования: {safe_text(str(exc))}", file=sys.stderr)
        return 1
    finally:
        app.state.manager.shutdown()

    payload: dict[str, Any] = {
        "status": session.status.value,
        "totals": {
            "items": len(session.items),
            "bytes": sum(item.size_bytes for item in session.items.values()),
        },
        "warnings": session.warnings,
        "items": [
            {
                "id": item.id,
                "rule_id": item.rule_id,
                "path": display_path(item.path, config.home),
                "size_bytes": item.size_bytes,
                "risk": item.risk.value,
                "warnings": list(item.warnings),
            }
            for item in sorted(
                session.items.values(),
                key=lambda item: item.size_bytes,
                reverse=True,
            )
        ],
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(
        f"Найдено: {payload['totals']['items']} пунктов, "
        f"{human_bytes(payload['totals']['bytes'])}"
    )
    for item in payload["items"]:
        print(f"  {human_bytes(item['size_bytes']):>10}  {item['path']}")
    for warning in session.warnings:
        print(f"! {warning}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    top_level = {"-h", "--help", "--version"}
    if not args or (args[0] not in {"serve", "scan"} and args[0] not in top_level):
        args.insert(0, "serve")

    parser = _build_parser()
    namespace = parser.parse_args(args)
    if namespace.command == "scan":
        return _scan(namespace)
    return _serve(namespace)


if __name__ == "__main__":
    raise SystemExit(main())
