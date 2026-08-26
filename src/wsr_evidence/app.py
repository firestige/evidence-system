"""Application assembly root."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException

from wsr_evidence.admission.service import AdmissionService
from wsr_evidence.query.postgresql import PostgresQueryReadModel
from wsr_evidence.query.service import QueryService
from wsr_evidence.storage.postgresql import PostgresStorage
from wsr_evidence.transport.http import router
from wsr_evidence.transport.otlp import OtlpIngestor, create_otlp_router
from wsr_evidence.transport.query import create_query_router, query_transport_error


def create_app(
    *,
    otlp_ingestor: OtlpIngestor | None = None,
    query_service: QueryService | None = None,
    database_url: str | None = None,
) -> FastAPI:
    storage: PostgresStorage | None = None
    query_storage: PostgresQueryReadModel | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal query_storage, storage
        if database_url is not None and (otlp_ingestor is None or query_service is None):
            storage = await PostgresStorage.open(database_url)
            if otlp_ingestor is None:
                app.state.otlp_ingestor = OtlpIngestor(AdmissionService(storage))
            if query_service is None:
                query_storage = PostgresQueryReadModel.from_storage(storage)
                app.state.query_service = QueryService(query_storage)
        yield
        if query_storage is not None:
            await query_storage.close()
        if storage is not None:
            await storage.close()

    app = FastAPI(title="wsr-evidence", version="0.1.0", lifespan=lifespan)
    app.add_exception_handler(StarletteHTTPException, query_transport_error)  # type: ignore[arg-type]
    app.include_router(router)
    if otlp_ingestor is not None:
        app.state.otlp_ingestor = otlp_ingestor
    if query_service is not None:
        app.state.query_service = query_service
    app.include_router(create_otlp_router())
    app.include_router(create_query_router())
    return app
