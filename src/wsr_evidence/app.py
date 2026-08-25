"""Application assembly root."""

from fastapi import FastAPI

from wsr_evidence.transport.http import router
from wsr_evidence.transport.otlp import OtlpIngestor, create_otlp_router


def create_app(*, otlp_ingestor: OtlpIngestor | None = None) -> FastAPI:
    app = FastAPI(title="wsr-evidence", version="0.1.0")
    app.include_router(router)
    if otlp_ingestor is not None:
        app.include_router(create_otlp_router(otlp_ingestor))
    return app
