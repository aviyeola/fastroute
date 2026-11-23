from __future__ import annotations
from typing import Any, Optional


class FastRouteError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False, details: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}

    def to_envelope(self) -> dict:
        return {
            "ok": False,
            "data": None,
            "warnings": [],
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "details": self.details,
            },
        }


def envelope_ok(data: Any = None, warnings: list[str] | None = None) -> dict:
    return {"ok": True, "data": data, "warnings": warnings or [], "error": None}
