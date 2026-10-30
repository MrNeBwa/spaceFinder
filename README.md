# spaceFinder

Локальный помощник очистки диска для Linux: сканирует известные «мусорные»
каталоги, показывает занятое место и позволяет удалять выбранные пункты.

По умолчанию удаление **перемещает объекты в корзину** freedesktop.org.
Место при этом освобождается только после очистки корзины — пункты правила
«Корзина» удаляются окончательно. Безвозвратное удаление выключено и требует
отдельного флага и подтверждения. Во время тестов реальные удаления запрещены.

## Установка

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Требуется Python 3.11+.

## Запуск

Сначала безопасный режим — приложение только показывает, что будет удалено:

```bash
python -m spacefinder --dry-run
```

Обычный запуск (удаление в корзину):

```bash
python -m spacefinder
```

Открывается `http://127.0.0.1:8765/`. Сервер слушает только localhost.

Полезные флаги:

```bash
python -m spacefinder --port 9000 --no-browser
python -m spacefinder --allow-permanent   # включает безвозвратное удаление
python -m spacefinder scan --json         # сканирование без сервера
```

Переменные окружения: `SPACEFINDER_HOST`, `SPACEFINDER_PORT`,
`SPACEFINDER_DRY_RUN`, `SPACEFINDER_ALLOW_PERMANENT`,
`SPACEFINDER_MAX_ITEMS`, `SPACEFINDER_SCAN_TIMEOUT`,
`SPACEFINDER_HOME` (подмена домашнего каталога для демо).

## Что сканируется

- `~/.cache/*` — кэши приложений старше 30 дней;
- `~/.npm/_cacache`, `~/.local/share/pnpm/store`, `~/.bun/install/cache`,
  `~/.cargo/registry/cache`, `~/.cargo/git/db`;
- корзина `~/.local/share/Trash/files` — очистка корзины удаляет пункты
  окончательно вместе с их `.trashinfo`, иначе «перемещение в корзину» лишь
  переименовало бы их внутри неё; если среди выбранных есть пункты корзины,
  интерфейс требует отдельного подтверждения;
- старые `*.iso`, `*.img`, `*.AppImage`, `*.deb`, `*.rpm` в `~/Downloads`;
- системные кэши (`pacman`, journal) только показываются с готовыми командами.

## Безопасность

- Клиент не передаёт пути: только непрозрачные `item_id` в рамках сессии скана.
- Перед каждой операцией путь проверяется `PathPolicy`: allowlist корней,
  защищённые каталоги (`~`, `~/.ssh`, `~/.config`, `/etc`, …), запрет точек
  монтирования и выхода через symlink-каталоги.
- Симлинки не разыменовываются; удаляется только сама ссылка.
- Перед удалением сверяются `st_dev`/`st_ino`, записанные при сканировании:
  если путь заменили между сканом и удалением, операция отклоняется.
- Корзина пишет `files/<name>` и `info/<name>.trashinfo` по спецификации
  freedesktop.org; при ошибке перемещения `.trashinfo` и недописанная копия
  удаляются. Каталоги корзины не могут быть симлинками.
- API защищён случайным токеном, проверкой `Host`/`Origin` и CSP.
- Сервер принимает только `127.0.0.1`/`localhost`; это проверяется и в
  `AppConfig`, и в CLI.
- История операций пишется в `~/.local/state/spacefinder/audit.jsonl` с правами
  `0600`.
- Файловые операции удаления не держат `session.lock`, поэтому список
  результатов и отмена скана остаются отзывчивыми; параллельное удаление
  в одной сессии возвращает `409 delete_in_progress`.

## API

Все `/api/*`, кроме `/api/health`, требуют заголовок
`X-Spacefinder-Token` (токен встроен в HTML-страницу).

| Метод | Путь | Назначение |
|---|---|---|
| `GET` | `/api/health` | проверка живости, без токена |
| `GET` | `/api/config` | `dry_run`, `allow_permanent`, версия |
| `GET` | `/api/disk` | занято/свободно |
| `GET` | `/api/rules` | реестр правил |
| `POST` | `/api/scans` | `{ "rule_ids": [...] }` → `202 {scan_id}` |
| `GET` | `/api/scans/{id}` | прогресс и пункты |
| `POST` | `/api/scans/{id}/cancel` | отмена скана |
| `POST` | `/api/scans/{id}/delete` | `{item_ids, mode, confirm}` |
| `GET` | `/api/history?limit=50` | последние записи аудита |

`mode`: `trash` (по умолчанию), `permanent` (нужен
`--allow-permanent`), `dry_run` (ничего не меняет).

## Тесты

```bash
python -m pytest -q   # или pytest -q, если команда доступна в venv
```

Фикстура в `tests/conftest.py` подменяет `safe_move`, `safe_rmtree` и
`safe_unlink` так, что любая операция вне `tmp_path` валит тест. Поэтому
тесты не удаляют пользовательские файлы. Дополнительно
`tests/test_no_destructive_calls.py` проверяет, что деструктивные вызовы
остаются только в `core/fsops.py` и в контролируемых местах корзины.

Полный локальный прогон:

```bash
python -m pytest -q
ruff check src tests
mypy src
python -m build
```

CI (`.github/workflows/ci.yml`) гоняет линтер, типы и тесты на Python
3.11–3.13, а также проверяет, что после тестов не тронут контрольный файл
в подменённом `$HOME`.

Проверка вручную в песочнице:

```bash
mkdir -p /tmp/spacefinder-demo/.cache/pip
dd if=/dev/zero of=/tmp/spacefinder-demo/.cache/pip/blob bs=1M count=2
touch -d '40 days ago' /tmp/spacefinder-demo/.cache/pip
SPACEFINDER_HOME=/tmp/spacefinder-demo python -m spacefinder --dry-run --no-browser
# открыть http://127.0.0.1:8765/, просканировать, выбрать, «Переместить в корзину»
# в dry-run изменения не применятся; для реальной корзины уберите --dry-run
# затем проверьте /tmp/spacefinder-demo/.local/share/Trash/info/*.trashinfo
```

## Структура

```
src/spacefinder/
  app.py              сборка FastAPI и зависимостей
  cli.py              serve/scan
  config.py           конфигурация и XDG-пути
  api/                схемы, роуты, токен
  core/
    models.py         доменные модели
    rules.py          реестр правил
    scanner.py        обход ФС и подсчёт размеров
    scan_manager.py   фоновые сессии сканирования
    path_policy.py    allowlist и защищённые пути
    fsops.py          единственная точка удаления
    trash.py          корзина freedesktop.org
    deleter.py        сервис удаления и аудит
    history.py        JSONL-журнал
  web/                HTML/CSS/JS без сборки
tests/                pytest, песочница tmp_path
```

## Ограничения

- Системные пути под root не удаляются — только подсказки с командами.
- Нет восстановления из корзины (это делается файловым менеджером).
- Веб-интерфейс рассчитан на один локальный пользователь.
