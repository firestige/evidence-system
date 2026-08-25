"""Stable internal input seam for later query and retention waves."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

CORE_READ_MODEL_VERSION = "1.0.0"
QUERY_CONTRACT_REVISION = "0.1.0"
RETENTION_POLICY_REVISION = "1.0.0"

DEFAULT_PAGE_LIMIT = 100
MAX_PAGE_LIMIT = 200
DEFAULT_SNAPSHOT_LEASE_TTL = timedelta(seconds=60)
DEFAULT_SNAPSHOT_LEASE_LIMIT = 4

DEFAULT_RAW_DEBUG_TTL = timedelta(0)
DEFAULT_TRACE_DETAIL_TTL = timedelta(days=30)
DEFAULT_FACTUAL_PROJECTION_TTL = timedelta(days=365)
DEFAULT_RETENTION_BATCH_SIZE = 500
DEFAULT_RETENTION_INTERVAL = timedelta(seconds=60)

Scalar = str | int | float | bool | None
OwnerKey = tuple[Scalar, ...]


class Completeness(StrEnum):
    FINAL = "FINAL"
    LOWER_BOUND = "LOWER_BOUND"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNAVAILABLE = "UNAVAILABLE"


class Availability(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class ExpiryState(StrEnum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"


class ResourceClass(StrEnum):
    RAW_DEBUG = "RAW_DEBUG"
    ACCEPTED_PROVENANCE = "ACCEPTED_PROVENANCE"
    TRACE_DETAIL = "TRACE_DETAIL"
    FACTUAL_PROJECTION = "FACTUAL_PROJECTION"


@dataclass(frozen=True, slots=True)
class TruthState:
    completeness: Completeness | None
    availability: Availability
    expiry: ExpiryState
    expires_at: datetime | None

    def __post_init__(self) -> None:
        _require_aware(self.expires_at, "expires_at", optional=True)
        if self.expiry is ExpiryState.EXPIRED:
            if self.availability is not Availability.UNAVAILABLE:
                raise ValueError("expired detail must be unavailable")
            if self.expires_at is None:
                raise ValueError("expired detail requires its expiry instant")
            return
        expected = (
            Availability.UNAVAILABLE
            if self.completeness is Completeness.UNAVAILABLE
            else Availability.AVAILABLE
        )
        if self.availability is not expected:
            raise ValueError("active availability must preserve recorded completeness")


def _whole_days(value: timedelta) -> bool:
    return value.microseconds == 0 and value.seconds == 0


def _bounded_ttl(
    value: timedelta | None,
    *,
    name: str,
    minimum: timedelta,
    maximum: timedelta,
    never_allowed: bool,
) -> None:
    if value is None:
        if never_allowed:
            return
        raise ValueError(f"{name} cannot be NEVER")
    if not _whole_days(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside its whole-day range")


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    raw_debug_ttl: timedelta = DEFAULT_RAW_DEBUG_TTL
    trace_detail_ttl: timedelta | None = DEFAULT_TRACE_DETAIL_TTL
    factual_projection_ttl: timedelta | None = DEFAULT_FACTUAL_PROJECTION_TTL
    batch_size: int = DEFAULT_RETENTION_BATCH_SIZE
    interval: timedelta = DEFAULT_RETENTION_INTERVAL
    revision: str = field(init=False, default=RETENTION_POLICY_REVISION)
    accepted_provenance_ttl: None = field(init=False, default=None)

    def __post_init__(self) -> None:
        _bounded_ttl(
            self.raw_debug_ttl,
            name="raw_debug_ttl",
            minimum=timedelta(0),
            maximum=timedelta(days=1),
            never_allowed=False,
        )
        _bounded_ttl(
            self.trace_detail_ttl,
            name="trace_detail_ttl",
            minimum=timedelta(days=1),
            maximum=timedelta(days=365),
            never_allowed=True,
        )
        _bounded_ttl(
            self.factual_projection_ttl,
            name="factual_projection_ttl",
            minimum=timedelta(days=30),
            maximum=timedelta(days=3650),
            never_allowed=True,
        )
        if not 1 <= self.batch_size <= 1000:
            raise ValueError("batch_size must be in [1,1000]")
        if self.interval.microseconds or not timedelta(seconds=10) <= self.interval <= timedelta(
            seconds=3600
        ):
            raise ValueError("interval must be whole seconds in [10,3600]")


@dataclass(frozen=True, slots=True)
class SnapshotPage[ResourceT]:
    contract_revision: str
    read_model_revision: str
    snapshot_id: str
    resources: tuple[ResourceT, ...]
    next_cursor: str | None

    def __post_init__(self) -> None:
        if self.contract_revision != QUERY_CONTRACT_REVISION:
            raise ValueError("snapshot Contract revision mismatch")
        if self.read_model_revision != CORE_READ_MODEL_VERSION:
            raise ValueError("snapshot read-model revision mismatch")
        if not self.snapshot_id:
            raise ValueError("snapshot_id must be nonempty")


@dataclass(frozen=True, slots=True)
class ExpiryRecord:
    resource_class: ResourceClass
    owner_key: OwnerKey
    source_identity: tuple[str, str]
    resource_kind: str
    recorded_at: datetime
    compatibility: tuple[tuple[str, Scalar], ...]
    policy_revision: str
    expired_at: datetime

    def __post_init__(self) -> None:
        _validate_owner_key(self.owner_key)
        _require_aware(self.recorded_at, "recorded_at")
        _require_aware(self.expired_at, "expired_at")
        if not self.resource_kind or not self.policy_revision:
            raise ValueError("expiry marker coordinates must be nonempty")


def _require_aware(value: datetime | None, name: str, *, optional: bool = False) -> None:
    if value is None:
        if optional:
            return
        raise ValueError(f"{name} is required")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _validate_owner_key(owner_key: OwnerKey) -> None:
    if not 1 <= len(owner_key) <= 16:
        raise ValueError("owner key must contain 1 through 16 scalars")
    for value in owner_key:
        if isinstance(value, str) and not 1 <= len(value.encode("utf-8")) <= 256:
            raise ValueError("owner key strings must contain 1 through 256 UTF-8 bytes")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("owner key numbers must be finite")


def _canonical_key(owner_key: OwnerKey) -> str:
    _validate_owner_key(owner_key)
    return json.dumps(owner_key, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class ExpiryBatch:
    batch_identity: str
    resource_class: ResourceClass
    policy_revision: str
    cutoff: datetime
    owner_keys: tuple[OwnerKey, ...]

    @classmethod
    def create(
        cls,
        *,
        resource_class: ResourceClass,
        policy_revision: str,
        cutoff: datetime,
        owner_keys: tuple[OwnerKey, ...],
    ) -> ExpiryBatch:
        if resource_class is ResourceClass.ACCEPTED_PROVENANCE:
            raise ValueError("accepted provenance cannot expire")
        if not policy_revision:
            raise ValueError("policy_revision must be nonempty")
        _require_aware(cutoff, "cutoff")
        if len(owner_keys) > 1000:
            raise ValueError("expiry batch cannot exceed 1000 owner keys")
        canonical = sorted((_canonical_key(owner_key), owner_key) for owner_key in owner_keys)
        if any(left[0] == right[0] for left, right in zip(canonical, canonical[1:], strict=False)):
            raise ValueError("expiry batch owner keys must be unique")
        ordered = tuple(owner_key for _, owner_key in canonical)
        normalized_cutoff = cutoff.astimezone(UTC).isoformat().replace("+00:00", "Z")
        encoded = json.dumps(
            [resource_class.value, policy_revision, normalized_cutoff, ordered],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        return cls(
            batch_identity=hashlib.sha256(encoded).hexdigest(),
            resource_class=resource_class,
            policy_revision=policy_revision,
            cutoff=cutoff.astimezone(UTC),
            owner_keys=ordered,
        )


@dataclass(frozen=True, slots=True)
class ExpiryResult:
    batch_identity: str
    selected: int
    expired: int
    already_expired: int

    def __post_init__(self) -> None:
        if len(self.batch_identity) != 64 or any(
            character not in "0123456789abcdef" for character in self.batch_identity
        ):
            raise ValueError("batch_identity must be lower-case SHA-256 hex")
        if min(self.selected, self.expired, self.already_expired) < 0:
            raise ValueError("expiry counts must be nonnegative")
        if self.expired + self.already_expired != self.selected:
            raise ValueError("expiry result must exactly partition selected resources")


@runtime_checkable
class QueryExpiryReadModel[ResourceT](Protocol):
    async def acquire_snapshot(
        self,
        *,
        query: str,
        filters: tuple[tuple[str, str], ...],
        limit: int,
        clock_now: datetime,
    ) -> SnapshotPage[ResourceT]: ...

    async def continue_snapshot(
        self, *, cursor: str, clock_now: datetime
    ) -> SnapshotPage[ResourceT]: ...

    async def read_expiry(
        self,
        *,
        resource_class: ResourceClass,
        owner_key: OwnerKey,
        snapshot_id: str,
    ) -> ExpiryRecord | None: ...


@runtime_checkable
class ExpiryMaintenance(Protocol):
    async def plan_expiry(
        self,
        *,
        resource_class: ResourceClass,
        policy_revision: str,
        cutoff: datetime,
        limit: int,
    ) -> ExpiryBatch: ...

    async def apply_expiry(self, *, batch: ExpiryBatch, clock_now: datetime) -> ExpiryResult: ...


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
