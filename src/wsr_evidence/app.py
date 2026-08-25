"""Application assembly root."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from wsr_evidence.admission.service import AdmissionService
from wsr_evidence.storage.postgresql import PostgresStorage
from wsr_evidence.transport.http import router
from wsr_evidence.transport.otlp import OtlpIngestor, create_otlp_router


def create_app(
    *, otlp_ingestor: OtlpIngestor | None = None, database_url: str | None = None
) -> FastAPI:
    storage: PostgresStorage | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal storage
        if otlp_ingestor is None and database_url is not None:
            storage = await PostgresStorage.open(database_url)
            app.state.otlp_ingestor = OtlpIngestor(AdmissionService(storage))
        yield
        if storage is not None:
            await storage.close()

    app = FastAPI(title="wsr-evidence", version="0.1.0", lifespan=lifespan)
    app.include_router(router)
    if otlp_ingestor is not None:
        app.state.otlp_ingestor = otlp_ingestor
    app.include_router(create_otlp_router())
    return app
