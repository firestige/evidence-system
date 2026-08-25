"""Stable internal input seam for later query and retention waves."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

CORE_READ_MODEL_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class StoredEffect:
    kind: str
    key: tuple[Any, ...]
    payload: dict[str, Any]
    source_identity: tuple[str, str]
    recorded_at: datetime


class CoreReadModel(Protocol):
    async def scan_effects(
        self, *, kind: str, after_key: tuple[Any, ...] | None, limit: int
    ) -> tuple[StoredEffect, ...]: ...
