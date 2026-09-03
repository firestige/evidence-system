"""Application assembly root."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException

from wsr_evidence.admission.service import AdmissionService
from wsr_evidence.query.postgresql import PostgresQueryReadModel
from wsr_evidence.query.service import QueryService
from wsr_evidence.retention.postgresql import PostgresRetentionMaintenance
from wsr_evidence.retention.scheduler import RetentionRunner, run_retention_loop
from wsr_evidence.retention.service import RetentionService
from wsr_evidence.storage.postgresql import PostgresStorage
from wsr_evidence.storage.read_model import DeliveryRetentionPolicy
from wsr_evidence.transport.http import router
from wsr_evidence.transport.otlp import OtlpIngestor, create_otlp_router
from wsr_evidence.transport.query import create_query_router, query_transport_error


def create_app(
    *,
    otlp_ingestor: OtlpIngestor | None = None,
    query_service: QueryService | None = None,
    retention_runner: RetentionRunner | None = None,
    retention_policy: DeliveryRetentionPolicy | None = None,
    database_url: str | None = None,
) -> FastAPI:
    storage: PostgresStorage | None = None
    query_storage: PostgresQueryReadModel | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal query_storage, storage
        policy = retention_policy or DeliveryRetentionPolicy()
        selected_retention_runner = retention_runner
        retention_task: asyncio.Task[None] | None = None
        if database_url is not None and (
            otlp_ingestor is None or query_service is None or selected_retention_runner is None
        ):
            storage = await PostgresStorage.open(database_url)
            if otlp_ingestor is None:
                app.state.otlp_ingestor = OtlpIngestor(AdmissionService(storage))
            if query_service is None:
                query_storage = PostgresQueryReadModel.from_storage(storage)
                app.state.query_service = QueryService(query_storage)
            if selected_retention_runner is None:
                selected_retention_runner = RetentionService(
                    PostgresRetentionMaintenance.from_storage(storage), policy=policy
                )
        if selected_retention_runner is not None:
            retention_task = asyncio.create_task(
                run_retention_loop(selected_retention_runner, interval=policy.interval)
            )
        try:
            yield
        finally:
            if retention_task is not None:
                retention_task.cancel()
                with suppress(asyncio.CancelledError):
                    await retention_task
            if query_storage is not None:
                await query_storage.close()
            if storage is not None:
                await storage.close()

    app = FastAPI(title="wsr-evidence", version="0.1.1", lifespan=lifespan)
    app.add_exception_handler(StarletteHTTPException, query_transport_error)  # type: ignore[arg-type]
    app.include_router(router)
    if otlp_ingestor is not None:
        app.state.otlp_ingestor = otlp_ingestor
    if query_service is not None:
        app.state.query_service = query_service
    app.include_router(create_otlp_router())
    app.include_router(create_query_router())
    return app
