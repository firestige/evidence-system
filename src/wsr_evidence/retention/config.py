"""Fail-closed startup projection of the published retention environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from typing import cast

from wsr_evidence.storage.read_model import RetentionPolicy


def _duration(name: str, default: str, *, never: bool) -> timedelta | None:
    value = os.environ.get(name, default)
    if value == "NEVER":
        if never:
            return None
        raise ValueError(f"{name} cannot be NEVER")
    if value == "PT0S":
        return timedelta(0)
    if len(value) < 3 or value[0] != "P" or value[-1] != "D" or not value[1:-1].isdigit():
        raise ValueError(f"{name} must be PT0S, P<n>D, or NEVER when allowed")
    return timedelta(days=int(value[1:-1]))


def _integer(name: str, default: str) -> int:
    value = os.environ.get(name, default)
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


@dataclass(frozen=True, slots=True)
class RetentionSettings:
    policy: RetentionPolicy

    @classmethod
    def from_environment(cls) -> RetentionSettings:
        if "WSR_EVIDENCE_ACCEPTED_PROVENANCE_TTL" in os.environ:
            raise ValueError("accepted provenance retention is not configurable")
        return cls(
            policy=RetentionPolicy(
                raw_debug_ttl=cast(
                    timedelta,
                    _duration("WSR_EVIDENCE_RAW_DEBUG_TTL", "PT0S", never=False),
                ),
                trace_detail_ttl=_duration("WSR_EVIDENCE_TRACE_DETAIL_TTL", "P30D", never=True),
                factual_projection_ttl=_duration(
                    "WSR_EVIDENCE_FACTUAL_PROJECTION_TTL", "P365D", never=True
                ),
                batch_size=_integer("WSR_EVIDENCE_RETENTION_BATCH_SIZE", "500"),
                interval=timedelta(
                    seconds=_integer("WSR_EVIDENCE_RETENTION_INTERVAL_SECONDS", "60")
                ),
            )
        )
