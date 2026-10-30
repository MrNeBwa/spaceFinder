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
    expected = request.app.state.token
    if x_spacefinder_token is None or not hmac.compare_digest(
        x_spacefinder_token, expected
    ):
        raise ApiError(403, "unauthorized", "Неверный или отсутствующий токен")
