"""Unified response envelope {code, message, data, now_ts} and API errors (CONTRACT §1)."""
from __future__ import annotations

import time
from typing import Any

from fastapi.responses import JSONResponse

OK = 0
BAD_REQUEST = 4400
UNAUTHORIZED = 4401
FORBIDDEN = 4403
NOT_FOUND = 4404
CONFLICT = 4409
SANDBOX_FULL = 4122
QUOTA = 4290
INTERNAL = 5000

HTTP_STATUS = {
    BAD_REQUEST: 400,
    UNAUTHORIZED: 401,
    FORBIDDEN: 403,
    NOT_FOUND: 404,
    CONFLICT: 409,
    SANDBOX_FULL: 429,
    QUOTA: 402,
    INTERNAL: 500,
}


class ApiError(Exception):
    def __init__(self, code: int, message: str, data: Any = None, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data
        self.status = status or HTTP_STATUS.get(code, 400)


def bad_request(msg: str = "Bad request") -> ApiError:
    return ApiError(BAD_REQUEST, msg)


def not_found(msg: str = "Not found") -> ApiError:
    return ApiError(NOT_FOUND, msg)


def forbidden(msg: str = "Forbidden") -> ApiError:
    return ApiError(FORBIDDEN, msg)


def unauthorized(msg: str = "Authentication error") -> ApiError:
    return ApiError(UNAUTHORIZED, msg)


def conflict(msg: str = "Conflict") -> ApiError:
    return ApiError(CONFLICT, msg)


def body(data: Any = None, code: int = OK, message: str = "Success.") -> dict[str, Any]:
    return {"code": code, "message": message, "data": data, "now_ts": time.time()}


def ok(data: Any = None) -> dict[str, Any]:
    return body(data)


def error_response(err: ApiError) -> JSONResponse:
    return JSONResponse(body(err.data, err.code, err.message), status_code=err.status)
