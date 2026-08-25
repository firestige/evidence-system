"""Local process entry point."""

import uvicorn

from wsr_evidence.app import create_app
from wsr_evidence.config import RuntimeSettings


def main() -> None:
    settings = RuntimeSettings.from_environment()
    uvicorn.run(
        create_app(database_url=settings.database_url), host=settings.host, port=settings.port
    )
