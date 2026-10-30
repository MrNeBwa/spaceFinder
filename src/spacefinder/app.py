"""Сборка FastAPI-приложения и всех зависимостей."""

from __future__ import annotations

import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .api.errors import ApiError
from .api.routes import router
from .config import AppConfig
from .core.deleter import DeleteService
from .core.format import safe_json, safe_text
from .core.history import HistoryLog
from .core.path_policy import PathPolicy
from .core.rules import default_rules, rules_by_id
from .core.scan_manager import ScanManager
from .core.scanner import Scanner
from .core.trash import TrashService

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"
STATIC_ALLOWLIST = frozenset({"app.js", "style.css", "i18n/ru.json"})

_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'"
)


def _protected_paths(config: AppConfig) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    exact = (
        Path("/"),
        Path("/home"),
        Path("/boot"),
        Path("/etc"),
        Path("/usr"),
        Path("/var"),
        Path("/proc"),
        Path("/sys"),
        Path("/dev"),
        Path("/run"),
        config.home,
        config.data_home,
        config.state_home,
    )
    subtrees = (
        config.home / ".ssh",
        config.home / ".gnupg",
        config.home / ".config",
        config.home / "Documents",
        config.state_home,
        config.data_home / "Trash" / "info",
    )
    return exact, subtrees


def _validate_test_sandbox(config: AppConfig) -> None:
    """Не дать тестовому режиму читать и писать вне test_root."""

    if not config.test_mode:
        return
    root = config.test_root
    if root is None:
        raise ValueError("test_mode требует явного test_root")
    if root == Path("/"):
        raise ValueError("test_root не может быть корнем файловой системы")
    for label, path in (
        ("home", config.home),
        ("data_home", config.data_home),
        ("state_home", config.state_home),
    ):
        if path != root and not path.is_relative_to(root):
            raise ValueError(
                f"test_mode: {label} находится вне test_root: {path}"
            )


def create_app(config: AppConfig | None = None) -> FastAPI:
    config = config or AppConfig.from_env()
    _validate_test_sandbox(config)

    rules = default_rules(config.home, config.data_home)
    mapping = rules_by_id(rules)

    allowed_roots = [root for rule in rules for root in rule.roots]
    allowed_roots.append(config.data_home / "Trash" / "files")
    protected_exact, protected_subtrees = _protected_paths(config)
    policy = PathPolicy(
        allowed_roots,
        protected_exact,
        test_root=config.test_root if config.test_mode else None,
        protected_subtrees=protected_subtrees,
    )

    scanner = Scanner(
        rules,
        max_items=config.max_items,
        max_seconds=config.scan_timeout,
    )
    manager = ScanManager(scanner, ttl=config.session_ttl)
    history = HistoryLog(config.state_home / "spacefinder" / "audit.jsonl")
    trash = TrashService(config.data_home, policy)
    delete_service = DeleteService(
        trash,
        policy,
        history,
        allow_permanent=config.allow_permanent,
        dry_run=config.dry_run,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        manager.shutdown()

    app = FastAPI(
        title="spaceFinder",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    app.state.config = config
    app.state.version = __version__
    app.state.rules = rules
    app.state.rules_by_id = mapping
    app.state.policy = policy
    app.state.scanner = scanner
    app.state.manager = manager
    app.state.history = history
    app.state.trash = trash
    app.state.delete_service = delete_service
    app.state.token = secrets.token_urlsafe(32)

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        host = request.headers.get("host", "").lower()
        if host not in config.allowed_hosts:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "code": "invalid_host",
                        "message": "Недопустимый заголовок Host",
                        "details": {},
                    }
                },
            )
        origin = request.headers.get("origin")
        if (
            request.method == "POST"
            and origin
            and origin.lower() not in config.allowed_origins
        ):
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "unauthorized",
                        "message": "Запрос с чужого Origin запрещён",
                        "details": {},
                    }
                },
            )

        response = await call_next(request)
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ApiError)
    async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.as_body())

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Некорректный запрос",
                    "details": {
                        "errors": safe_json(jsonable_encoder(exc.errors()))
                    },
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        _: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "http_error"
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": code,
                    "message": safe_text(str(exc.detail)),
                    "details": {},
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Необработанная ошибка", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "Внутренняя ошибка приложения",
                    "details": {},
                }
            },
        )

    app.include_router(router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        html = html.replace("__SPACEFINDER_TOKEN__", app.state.token)
        return HTMLResponse(
            content=html,
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/static/{file_path:path}")
    def static_file(file_path: str) -> FileResponse:
        if file_path not in STATIC_ALLOWLIST:
            raise ApiError(404, "not_found", "Файл не найден")
        target = WEB_DIR / file_path
        if not target.is_file():
            raise ApiError(404, "not_found", "Файл не найден")
        return FileResponse(target)

    return app
