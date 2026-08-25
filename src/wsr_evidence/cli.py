"""Local process entry point."""

import uvicorn

from wsr_evidence.config import RuntimeSettings


def main() -> None:
    settings = RuntimeSettings.from_environment()
    uvicorn.run(
        "wsr_evidence.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
    )
