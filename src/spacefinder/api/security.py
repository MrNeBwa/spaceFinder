"""Локальная аутентификация API."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Header, Request

from .errors import ApiError


async def require_token(
    request: Request,
    x_spacefinder_token: Annotated[
        str | None, Header(alias="X-Spacefinder-Token")
    ] = None,
) -> None:
    expected = request.app.state.token.encode("ascii")
    # compare_digest для str падает на не-ASCII заголовках. Сравниваем
    # байты, чтобы произвольный заголовок давал 403, а не 500.
    provided = (x_spacefinder_token or "").encode("utf-8", errors="surrogateescape")
    if not hmac.compare_digest(provided, expected):
        raise ApiError(403, "unauthorized", "Неверный или отсутствующий токен")
