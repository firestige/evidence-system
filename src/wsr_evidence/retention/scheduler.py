"""Bounded process-lifetime scheduling for retention maintenance."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Protocol

from wsr_evidence.storage.read_model import ExpiryResult

LOGGER = logging.getLogger(__name__)


class RetentionRunner(Protocol):
    async def run_once(self) -> tuple[ExpiryResult, ...]: ...


async def run_retention_loop(runner: RetentionRunner, *, interval: timedelta) -> None:
    """Run immediately, then wait the frozen policy interval between bounded batches."""

    while True:
        try:
            await runner.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("retention maintenance iteration failed safely")
        await asyncio.sleep(interval.total_seconds())
