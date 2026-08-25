"""Application assembly root."""

from fastapi import FastAPI

from wsr_evidence.transport.http import router


def create_app() -> FastAPI:
    app = FastAPI(title="wsr-evidence", version="0.1.0")
    app.include_router(router)
    return app
