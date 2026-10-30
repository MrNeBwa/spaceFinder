"""Единый формат ошибок API."""

from __future__ import annotations

from typing import Any

from ..core.format import safe_json, safe_text


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}

    def as_body(self) -> dict[str, Any]:
        return {
            "error": {
                "code": safe_text(self.code),
                "message": safe_text(self.message),
                "details": safe_json(self.details),
            }
        }
