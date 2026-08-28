"""Fail-closed startup projection of the published retention environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from typing import cast

from wsr_evidence.storage.read_model import DeliveryRetentionPolicy


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
    policy: DeliveryRetentionPolicy

    @classmethod
    def from_environment(cls) -> RetentionSettings:
        retired_variables = (
            "WSR_EVIDENCE_TRACE_DETAIL_TTL",
            "WSR_EVIDENCE_FACTUAL_PROJECTION_TTL",
            "WSR_EVIDENCE_ACCEPTED_PROVENANCE_TTL",
        )
        configured = next((name for name in retired_variables if name in os.environ), None)
        if configured is not None:
            raise ValueError(f"{configured} is retired; configure Delivery retention as one unit")
        return cls(
            policy=DeliveryRetentionPolicy(
                raw_debug_ttl=cast(
                    timedelta,
                    _duration("WSR_EVIDENCE_RAW_DEBUG_TTL", "PT0S", never=False),
                ),
                delivery_ttl=_duration("WSR_EVIDENCE_DELIVERY_TTL", "P30D", never=True),
                batch_size=_integer("WSR_EVIDENCE_RETENTION_BATCH_SIZE", "500"),
                interval=timedelta(
                    seconds=_integer("WSR_EVIDENCE_RETENTION_INTERVAL_SECONDS", "60")
                ),
            )
        )
