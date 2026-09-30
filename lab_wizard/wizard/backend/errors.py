"""One shape for every error the API returns, so the page reads them one way.

Every non-2xx response is::

    {"detail": "what went wrong, for a person", "problems": [{"path": [...], "message": "..."}]}

``problems`` says *where*, when a request had fixable parts — a project's
settings, a request body that failed validation — and is empty otherwise. The
frontend's client (``src/lib/api``) turns this into an ``ApiError``.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

__all__ = ["ErrorBody", "Problem", "RequestProblem", "install_error_handlers"]


class Problem(BaseModel):
    """One fixable thing wrong with a request, and where it is."""

    path: list[str | int] = Field(default_factory=list)
    message: str


class ErrorBody(BaseModel):
    """What every error response holds."""

    detail: str
    problems: list[Problem] = Field(default_factory=list)


class RequestProblem(Exception):
    """A request the page can fix, with where each problem is."""

    def __init__(self, status_code: int, detail: str, problems: list[dict[str, Any]] | None = None) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.problems = problems or []


def _body(detail: Any, problems: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if isinstance(detail, dict) and "message" in detail:  # {"message", "problems"}
        return _body(detail["message"], detail.get("problems"))
    text = detail if isinstance(detail, str) else str(detail)
    return ErrorBody(detail=text, problems=[Problem.model_validate(p) for p in problems or []]).model_dump()


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestProblem)
    async def _problem(_request: Request, exc: RequestProblem) -> JSONResponse:
        return JSONResponse(_body(exc.detail, exc.problems), status_code=exc.status_code)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(_body(exc.detail), status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _invalid(_request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = [
            # FastAPI's loc starts with where the value came from: body, query, path.
            {"path": list(error.get("loc", ()))[1:], "message": error.get("msg", "invalid")}
            for error in exc.errors()
        ]
        summary = "; ".join(f"{'.'.join(map(str, p['path'])) or 'request'}: {p['message']}" for p in problems)
        return JSONResponse(_body(summary or "Invalid request", problems), status_code=422)
