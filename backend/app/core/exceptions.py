"""Application exceptions and their HTTP representation."""

from collections.abc import Mapping
from http import HTTPStatus
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import JsonValue

from app.schemas.errors import ErrorBody, ErrorResponse


class AppError(Exception):
    """Base exception for errors safe to expose through the API contract."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int = HTTPStatus.INTERNAL_SERVER_ERROR,
        details: Mapping[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = dict(details or {})


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Map an application error to the shared error envelope."""
    if not isinstance(exc, AppError):
        raise exc

    request_id = getattr(request.state, "request_id", None) or str(uuid4())
    payload = ErrorResponse(
        error=ErrorBody(
            code=exc.code,
            message=exc.message,
            request_id=request_id,
            details=exc.details,
        )
    )
    return JSONResponse(status_code=exc.status_code, content=payload.model_dump(mode="json"))


async def request_validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Map FastAPI/Pydantic request errors to the shared error envelope."""
    if not isinstance(exc, RequestValidationError):
        raise exc

    validation_details: list[JsonValue] = []
    for error in exc.errors():
        validation_details.append(
            {
                "type": str(error.get("type", "validation_error")),
                "location": [str(part) for part in error.get("loc", ())],
                "message": str(error.get("msg", "invalid request")),
            }
        )

    if request.url.path.endswith("/profile"):
        code = "VALIDATION_PROFILE_INVALID"
    elif "/agent-runs" in request.url.path:
        code = "VALIDATION_RUN_INVALID"
    elif "/tasks" in request.url.path:
        code = "VALIDATION_TASK_INVALID"
    elif "/reviews" in request.url.path:
        code = "VALIDATION_REVIEW_INVALID"
    elif "/eval/" in request.url.path or "/eval-runs" in request.url.path:
        code = "VALIDATION_EVAL_INVALID"
    else:
        code = "VALIDATION_REQUEST_INVALID"
    payload = ErrorResponse(
        error=ErrorBody(
            code=code,
            message="request validation failed",
            request_id=getattr(request.state, "request_id", None) or str(uuid4()),
            details={"errors": validation_details},
        )
    )
    return JSONResponse(
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        content=payload.model_dump(mode="json"),
    )


def register_exception_handlers(application: FastAPI) -> None:
    """Register application-specific HTTP exception mappings."""
    application.add_exception_handler(AppError, app_error_handler)
    application.add_exception_handler(RequestValidationError, request_validation_error_handler)
