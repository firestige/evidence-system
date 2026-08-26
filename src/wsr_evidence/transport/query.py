"""HTTP adapter for the versioned read-only Evidence query candidate."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from wsr_evidence.query.service import QueryError, QueryErrorCode, QueryService

ERROR_STATUS = {
    QueryErrorCode.INVALID_FILTER: 400,
    QueryErrorCode.INVALID_CURSOR: 400,
    QueryErrorCode.NOT_ACCEPTABLE: 406,
    QueryErrorCode.CURSOR_MISMATCH: 409,
    QueryErrorCode.CURSOR_EXPIRED: 410,
    QueryErrorCode.QUERY_BOUND_EXCEEDED: 413,
    QueryErrorCode.METHOD_NOT_ALLOWED: 405,
    QueryErrorCode.ROUTE_NOT_FOUND: 404,
    QueryErrorCode.QUERY_INTERNAL: 500,
    QueryErrorCode.QUERY_UNAVAILABLE: 503,
}


def _error(error: QueryError) -> JSONResponse:
    return JSONResponse(
        status_code=ERROR_STATUS[error.code],
        content={"error": {"code": error.code.value, "message": str(error)[:256]}},
    )


async def query_transport_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
    if request.url.path.startswith("/v1/evidence/"):
        code = (
            QueryErrorCode.METHOD_NOT_ALLOWED
            if error.status_code == 405
            else QueryErrorCode.ROUTE_NOT_FOUND
        )
        return _error(QueryError(code, "query route or method is not defined"))
    return JSONResponse(status_code=error.status_code, content={"detail": error.detail})


def _service(request: Request) -> QueryService:
    service = getattr(request.app.state, "query_service", None)
    if service is None:
        raise QueryError(QueryErrorCode.QUERY_UNAVAILABLE, "query storage is unavailable")
    return cast(QueryService, service)


async def _prepare(request: Request) -> QueryService:
    if await request.body():
        raise QueryError(QueryErrorCode.INVALID_FILTER, "query GET request body is prohibited")
    accept = request.headers.get("accept", "*/*")
    if not _accepts_json(accept):
        raise QueryError(QueryErrorCode.NOT_ACCEPTABLE, "application/json is required")
    return _service(request)


def _accepts_json(header: str) -> bool:
    for entry in header.lower().split(","):
        parts = [part.strip() for part in entry.split(";")]
        quality = 1.0
        for parameter in parts[1:]:
            if parameter.startswith("q="):
                try:
                    quality = float(parameter.removeprefix("q="))
                except ValueError:
                    quality = 0.0
        if quality > 0 and parts[0] in {"*/*", "application/*", "application/json"}:
            return True
    return False


def create_query_router() -> APIRouter:
    router = APIRouter()

    @router.get("/v1/evidence/facts")
    async def facts(request: Request) -> JSONResponse:
        try:
            service = await _prepare(request)
            result = await service.facts(list(request.query_params.multi_items()))
            return JSONResponse(content=result)
        except QueryError as error:
            return _error(error)
        except Exception:
            return _error(QueryError(QueryErrorCode.QUERY_INTERNAL, "query failed safely"))

    @router.get("/v1/evidence/traces")
    async def traces(request: Request) -> JSONResponse:
        try:
            service = await _prepare(request)
            result = await service.traces(list(request.query_params.multi_items()))
            return JSONResponse(content=result)
        except QueryError as error:
            return _error(error)
        except Exception:
            return _error(QueryError(QueryErrorCode.QUERY_INTERNAL, "query failed safely"))

    async def method_not_allowed() -> JSONResponse:
        return _error(QueryError(QueryErrorCode.METHOD_NOT_ALLOWED, "method is not allowed"))

    for path in ("/v1/evidence/facts", "/v1/evidence/traces"):
        router.add_api_route(
            path,
            method_not_allowed,
            methods=["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
            include_in_schema=False,
        )

    @router.api_route(
        "/v1/evidence/{unlisted_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        include_in_schema=False,
    )
    async def route_not_found(unlisted_path: str) -> JSONResponse:
        del unlisted_path
        return _error(QueryError(QueryErrorCode.ROUTE_NOT_FOUND, "query route is not defined"))

    return router
