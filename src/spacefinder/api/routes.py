"""HTTP-эндпоинты spaceFinder."""

from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from ..core.deleter import DeleteValidationError
from ..core.format import display_path, safe_json, safe_text
from .errors import ApiError
from .schemas import (
    ConfigOut,
    CreateScanRequest,
    DeleteRequest,
    DeleteResponse,
    DeleteResultOut,
    DiskOut,
    HistoryResponse,
    ItemOut,
    ProgressOut,
    RuleOut,
    ScanOut,
    TotalsOut,
)
from .security import require_token

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

_DELETE_ERROR_STATUS = {
    "confirmation_required": 400,
    "invalid_request": 400,
    "permanent_disabled": 403,
    "scan_not_ready": 409,
    "delete_in_progress": 409,
    "conflict": 409,
    "item_not_found": 404,
}


def _rules(request: Request):
    return request.app.state.rules


def _rules_by_id(request: Request):
    return request.app.state.rules_by_id


@router.get("/config", response_model=ConfigOut)
def get_config(request: Request) -> ConfigOut:
    config = request.app.state.config
    return ConfigOut(
        dry_run=config.dry_run,
        allow_permanent=config.allow_permanent,
        version=request.app.state.version,
        home=safe_text(str(config.home)),
    )


@router.get("/disk", response_model=DiskOut)
def get_disk(request: Request) -> DiskOut:
    config = request.app.state.config
    try:
        usage = shutil.disk_usage(config.home)
    except OSError as exc:
        raise ApiError(
            500,
            "disk_unavailable",
            f"Не удалось прочитать информацию о диске: {exc}",
        ) from exc
    percent = (usage.used / usage.total * 100.0) if usage.total else 0.0
    return DiskOut(
        total=usage.total,
        used=usage.used,
        free=usage.free,
        percent=round(percent, 1),
    )


@router.get("/rules", response_model=list[RuleOut])
def get_rules(request: Request) -> list[RuleOut]:
    return [
        RuleOut(
            id=rule.id,
            title=rule.title,
            description=rule.description,
            risk=rule.risk,
            default_selected=rule.default_selected,
            requires_privilege=rule.requires_privilege,
            commands=list(rule.info_commands),
        )
        for rule in _rules(request)
    ]


@router.post("/scans", status_code=202)
def create_scan(request: Request, payload: CreateScanRequest) -> JSONResponse:
    rules = _rules(request)
    known = {rule.id for rule in rules}
    privileged = {rule.id for rule in rules if rule.requires_privilege}
    requested = (
        payload.rule_ids
        if payload.rule_ids is not None
        else [rule.id for rule in rules if not rule.requires_privilege]
    )
    if not requested:
        raise ApiError(400, "invalid_request", "Не выбрано ни одного правила")

    unknown = [rule_id for rule_id in requested if rule_id not in known]
    if unknown:
        raise ApiError(400, "invalid_request", f"Неизвестное правило: {unknown[0]}")
    denied = [rule_id for rule_id in requested if rule_id in privileged]
    if denied:
        raise ApiError(
            400,
            "invalid_request",
            f"Правило требует root-прав и недоступно для удаления: {denied[0]}",
        )

    session = request.app.state.manager.start(requested)
    return JSONResponse(status_code=202, content={"scan_id": session.id})


@router.get("/scans/{scan_id}", response_model=ScanOut)
def get_scan(request: Request, scan_id: str) -> ScanOut:
    try:
        session = request.app.state.manager.get(scan_id)
    except KeyError as exc:
        raise ApiError(404, "scan_not_found", "Сессия сканирования не найдена") from exc

    config = request.app.state.config
    mapping = _rules_by_id(request)
    # Снимок состояния под коротким замком; realpath-проверки корзины
    # выполняем уже без него, чтобы не блокировать сканирование.
    with session.lock:
        items = sorted(
            session.items.values(), key=lambda item: item.size_bytes, reverse=True
        )
        deleted_ids = set(session.deleted_ids)
        status = session.status
        created_at = session.created_at
        finished_at = session.finished_at
        progress = ProgressOut(
            phase=session.progress.phase,
            items_found=session.progress.items_found,
            bytes_found=session.progress.bytes_found,
            current_path=display_path(session.progress.current_path, config.home)
            if session.progress.current_path
            else "",
            truncated=session.progress.truncated,
        )
        warnings = [safe_text(warning) for warning in session.warnings]

    item_outs = [
        ItemOut(
            id=item.id,
            rule_id=item.rule_id,
            rule_title=mapping[item.rule_id].title,
            path=display_path(item.path, config.home),
            kind=item.kind.value,
            risk=item.risk,
            size_bytes=item.size_bytes,
            apparent_bytes=item.apparent_bytes,
            mtime=item.mtime,
            default_selected=mapping[item.rule_id].default_selected,
            warnings=[safe_text(warning) for warning in item.warnings],
            deleted=item.id in deleted_ids,
            in_trash=request.app.state.trash.contains(item.path),
        )
        for item in items
    ]
    all_bytes = sum(item.size_bytes for item in items)
    default_bytes = sum(
        item.size_bytes for item in items if mapping[item.rule_id].default_selected
    )
    return ScanOut(
        scan_id=session.id,
        status=status,
        created_at=created_at,
        finished_at=finished_at,
        progress=progress,
        totals=TotalsOut(
            all_bytes=all_bytes,
            default_selected_bytes=default_bytes,
            items=len(items),
        ),
        items=item_outs,
        warnings=warnings,
    )


@router.post("/scans/{scan_id}/cancel", status_code=202)
def cancel_scan(request: Request, scan_id: str) -> JSONResponse:
    try:
        session = request.app.state.manager.cancel(scan_id)
    except KeyError as exc:
        raise ApiError(404, "scan_not_found", "Сессия сканирования не найдена") from exc
    return JSONResponse(
        status_code=202,
        content={"scan_id": session.id, "status": session.status.value},
    )


@router.post("/scans/{scan_id}/delete", response_model=DeleteResponse)
def delete_items(
    request: Request, scan_id: str, payload: DeleteRequest
) -> DeleteResponse:
    try:
        session = request.app.state.manager.get(scan_id)
    except KeyError as exc:
        raise ApiError(404, "scan_not_found", "Сессия сканирования не найдена") from exc

    service = request.app.state.delete_service
    try:
        results = service.delete(
            session,
            payload.item_ids,
            payload.mode,
            payload.confirm,
        )
    except DeleteValidationError as exc:
        status_code = _DELETE_ERROR_STATUS.get(exc.code, 400)
        raise ApiError(status_code, exc.code, exc.message) from exc

    return DeleteResponse(
        results=[
            DeleteResultOut(
                item_id=safe_text(result.item_id),
                status=result.status,
                freed_bytes=result.freed_bytes,
                target=safe_text(result.target) if result.target is not None else None,
                error_code=safe_text(result.error_code)
                if result.error_code is not None
                else None,
                message=safe_text(result.message) if result.message is not None else None,
            )
            for result in results
        ],
        freed_bytes=sum(
            result.freed_bytes
            for result in results
            if result.status.value in {"deleted", "simulated"}
        ),
    )


@router.get("/history", response_model=HistoryResponse)
def get_history(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> HistoryResponse:
    entries = request.app.state.history.tail(limit)
    return HistoryResponse(entries=[safe_json(entry) for entry in entries])
