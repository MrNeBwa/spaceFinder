#!/usr/bin/env bash
# Простой запуск spaceFinder из репозитория.
# Все аргументы передаются приложению, например:
#   ./run.sh --dry-run --no-browser
#   ./run.sh scan --json
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$SCRIPT_DIR"

if [[ -n "${SPACEFINDER_PYTHON:-}" ]]; then
  PYTHON="$SPACEFINDER_PYTHON"
elif [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
  PYTHON="$SCRIPT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
else
  echo "Ошибка: не найден Python 3.11+. Установите Python и повторите." >&2
  exit 1
fi

if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Ошибка: нужен Python 3.11+, найден: $("$PYTHON" --version 2>&1)" >&2
  exit 1
fi

# Если пакет ещё не установлен, но запускаем из исходников — добавим src.
if ! "$PYTHON" -c 'import spacefinder' >/dev/null 2>&1; then
  if [[ -d "$SCRIPT_DIR/src/spacefinder" ]]; then
    export PYTHONPATH="$SCRIPT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
  else
    echo "Ошибка: spaceFinder не установлен." >&2
    echo "Выполните: python3 -m venv .venv && .venv/bin/pip install -e ." >&2
    exit 1
  fi
fi

exec "$PYTHON" -m spacefinder "$@"
