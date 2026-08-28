"""Stable internal input seam for later query and retention waves."""

from __future__ import annotations

import hashlib
import json
import math
import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

CORE_READ_MODEL_VERSION = "1.0.0"
QUERY_CONTRACT_REVISION = "0.1.0"
TASK_READ_MODEL_VERSION = "2.0.0"
TASK_QUERY_CONTRACT_REVISION = "1.0.0"
RETENTION_POLICY_REVISION = "1.0.0"
DELIVERY_RETENTION_POLICY_REVISION = "2.0.0"

DEFAULT_PAGE_LIMIT = 100
MAX_PAGE_LIMIT = 200
DEFAULT_SNAPSHOT_LEASE_TTL = timedelta(seconds=60)
DEFAULT_SNAPSHOT_LEASE_LIMIT = 4

DEFAULT_RAW_DEBUG_TTL = timedelta(0)
DEFAULT_TRACE_DETAIL_TTL = timedelta(days=30)
DEFAULT_FACTUAL_PROJECTION_TTL = timedelta(days=365)
DEFAULT_RETENTION_BATCH_SIZE = 500
DEFAULT_RETENTION_INTERVAL = timedelta(seconds=60)
DEFAULT_DELIVERY_TTL = timedelta(days=30)

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


class TraceDetailState(StrEnum):
    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    EXPIRED = "EXPIRED"


class ResourceClass(StrEnum):
    RAW_DEBUG = "RAW_DEBUG"
    ACCEPTED_PROVENANCE = "ACCEPTED_PROVENANCE"
    TRACE_DETAIL = "TRACE_DETAIL"
    FACTUAL_PROJECTION = "FACTUAL_PROJECTION"


TRACE_RESOURCE_KINDS = frozenset({"NODE", "PARENT_EDGE", "LINK"})
FACTUAL_RESOURCE_KINDS = frozenset(
    {
        "EVENT_CONTRIBUTION",
        "FINDING_ASSERTION",
        "FINDING_TARGET",
        "FINDING_STATUS",
        "FINDING_FIX",
        "FINDING_RECHECK",
        "ROLE_LINEAGE",
        "DELIVERY_ROOT_BINDING",
        "MODEL_ATTRIBUTION",
    }
)


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
class DeliveryRetentionPolicy:
    """Current policy: raw privacy scrub plus Delivery-atomic physical deletion."""

    raw_debug_ttl: timedelta = DEFAULT_RAW_DEBUG_TTL
    delivery_ttl: timedelta | None = DEFAULT_DELIVERY_TTL
    batch_size: int = DEFAULT_RETENTION_BATCH_SIZE
    interval: timedelta = DEFAULT_RETENTION_INTERVAL
    revision: str = field(init=False, default=DELIVERY_RETENTION_POLICY_REVISION)

    def __post_init__(self) -> None:
        _bounded_ttl(
            self.raw_debug_ttl,
            name="raw_debug_ttl",
            minimum=timedelta(0),
            maximum=timedelta(days=1),
            never_allowed=False,
        )
        _bounded_ttl(
            self.delivery_ttl,
            name="delivery_ttl",
            minimum=timedelta(days=1),
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
        if (self.contract_revision, self.read_model_revision) not in {
            (QUERY_CONTRACT_REVISION, CORE_READ_MODEL_VERSION),
            (TASK_QUERY_CONTRACT_REVISION, TASK_READ_MODEL_VERSION),
        }:
            raise ValueError("snapshot Contract/read-model revision mismatch")
        if not self.snapshot_id:
            raise ValueError("snapshot_id must be nonempty")


@dataclass(frozen=True, slots=True)
class ExpiryOwner:
    resource_kind: str
    owner_key: OwnerKey

    def __post_init__(self) -> None:
        if not self.resource_kind:
            raise ValueError("expiry resource_kind must be nonempty")
        _validate_owner_key(self.owner_key)


def _validate_expiry_kind(resource_class: ResourceClass, resource_kind: str) -> None:
    allowed = {
        ResourceClass.RAW_DEBUG: frozenset({"RAW_DEBUG"}),
        ResourceClass.TRACE_DETAIL: TRACE_RESOURCE_KINDS,
        ResourceClass.FACTUAL_PROJECTION: FACTUAL_RESOURCE_KINDS,
    }.get(resource_class, frozenset())
    if resource_kind not in allowed:
        raise ValueError("resource kind is not valid for its expiry class")


def _is_hex(value: Scalar, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _all_strings(values: OwnerKey) -> bool:
    return all(isinstance(value, str) and bool(value) for value in values)


def _validate_expiry_owner(resource_class: ResourceClass, owner: ExpiryOwner) -> None:
    _validate_expiry_kind(resource_class, owner.resource_kind)
    key = owner.owner_key
    valid = False
    if resource_class is ResourceClass.RAW_DEBUG:
        valid = (
            len(key) == 2 and key[0] == "event" and isinstance(key[1], str) and bool(key[1])
        ) or (len(key) == 3 and key[0] == "span" and _is_hex(key[1], 32) and _is_hex(key[2], 16))
    elif resource_class is ResourceClass.TRACE_DETAIL:
        valid = {
            "NODE": len(key) == 2 and _is_hex(key[0], 32) and _is_hex(key[1], 16),
            "PARENT_EDGE": (
                len(key) == 3
                and _is_hex(key[0], 32)
                and _is_hex(key[1], 16)
                and _is_hex(key[2], 16)
            ),
            "LINK": (
                len(key) == 4
                and _is_hex(key[0], 32)
                and _is_hex(key[1], 16)
                and _is_hex(key[2], 32)
                and _is_hex(key[3], 16)
            ),
        }.get(owner.resource_kind, False)
    elif resource_class is ResourceClass.FACTUAL_PROJECTION:
        fixed_string_arity = {
            "EVENT_CONTRIBUTION": 2,
            "FINDING_ASSERTION": 2,
            "FINDING_STATUS": 3,
            "ROLE_LINEAGE": 2,
        }
        if owner.resource_kind in fixed_string_arity:
            valid = len(key) == fixed_string_arity[owner.resource_kind] and _all_strings(key)
        elif owner.resource_kind == "FINDING_TARGET":
            valid = (
                len(key) == 5
                and _all_strings(key[:4])
                and (key[4] is None or isinstance(key[4], str) and bool(key[4]))
            )
        elif owner.resource_kind in {"FINDING_FIX", "FINDING_RECHECK"}:
            valid = (
                len(key) == 6
                and _all_strings(key[:4])
                and (key[4] is None or isinstance(key[4], str) and bool(key[4]))
                and isinstance(key[5], str)
                and bool(key[5])
            )
        elif owner.resource_kind == "DELIVERY_ROOT_BINDING":
            valid = len(key) == 1 and _is_hex(key[0], 32)
        elif owner.resource_kind == "MODEL_ATTRIBUTION":
            valid = (
                len(key) == 6
                and _all_strings(key[:4])
                and _is_hex(key[4], 32)
                and _is_hex(key[5], 16)
            )
    if not valid:
        raise ValueError("owner key does not match its closed resource kind shape")


def _validate_compatibility(record: ExpiryRecord) -> None:
    pairs = record.compatibility
    if record.resource_class in {ResourceClass.RAW_DEBUG, ResourceClass.TRACE_DETAIL}:
        if pairs:
            raise ValueError("Raw and Trace expiry compatibility must be empty")
        return
    if not 3 <= len(pairs) <= 8 or tuple(key for key, _ in pairs[:3]) != (
        "family_schema",
        "event_name",
        "completeness",
    ):
        raise ValueError("factual expiry compatibility requires its three base pairs")
    keys = tuple(key for key, _ in pairs)
    if len(set(keys)) != len(keys):
        raise ValueError("expiry compatibility pair keys must be unique")
    event_name = pairs[1][1]
    dimension_orders = {
        "usage": ("C42", "C43", "C44", "C45"),
        "implementation.summary": ("I05", "I08", "I09", "I10"),
        "test.summary": ("C28", "C29"),
        "review.summary": ("C13", "C14"),
    }
    allowed_suffix: tuple[str, ...] = ()
    if record.resource_kind == "EVENT_CONTRIBUTION" and isinstance(event_name, str):
        allowed_suffix = dimension_orders.get(event_name, ())
    elif record.resource_kind == "FINDING_ASSERTION":
        allowed_suffix = ("C13", "C14")
    elif record.resource_kind == "MODEL_ATTRIBUTION":
        allowed_suffix = ("gen_ai.provider.name", "C57", "C30", "C06")
    elif record.resource_kind == "DELIVERY_ROOT_BINDING":
        allowed_suffix = ("delivery_id",)
    if record.resource_kind in {
        "EVENT_CONTRIBUTION",
        "FINDING_ASSERTION",
        "FINDING_TARGET",
        "FINDING_STATUS",
        "FINDING_FIX",
        "FINDING_RECHECK",
        "ROLE_LINEAGE",
    }:
        allowed_suffix = (*allowed_suffix, "trace_id")
    suffix = keys[3:]
    positions = [allowed_suffix.index(key) for key in suffix if key in allowed_suffix]
    if len(positions) != len(suffix) or positions != sorted(positions):
        raise ValueError("expiry compatibility dimensions are unknown or out of order")
    for key, value in pairs:
        if not isinstance(key, str) or not key or not _is_scalar(value):
            raise ValueError("expiry compatibility pairs must contain bounded scalars")


@dataclass(frozen=True, slots=True)
class ExpiryRecord:
    resource_class: ResourceClass
    owner_key: OwnerKey
    source_identity: tuple[str, str]
    resource_kind: str
    recorded_at: datetime
    compatibility: tuple[tuple[str, Scalar], ...]
    policy_revision: str
    expires_at: datetime
    expired_at: datetime

    def __post_init__(self) -> None:
        _validate_owner_key(self.owner_key)
        _validate_expiry_owner(
            self.resource_class,
            ExpiryOwner(resource_kind=self.resource_kind, owner_key=self.owner_key),
        )
        _require_aware(self.recorded_at, "recorded_at")
        _require_aware(self.expires_at, "expires_at")
        _require_aware(self.expired_at, "expired_at")
        if not self.resource_kind or not self.policy_revision:
            raise ValueError("expiry marker coordinates must be nonempty")
        _validate_compatibility(self)


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
        if not _is_scalar(value):
            raise ValueError("owner key values must be scalars")
        if isinstance(value, str) and not 1 <= len(value.encode("utf-8")) <= 256:
            raise ValueError("owner key strings must contain 1 through 256 UTF-8 bytes")
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and not (-9_007_199_254_740_991 <= value <= 9_007_199_254_740_991)
        ):
            raise ValueError("owner key integers must be interoperable")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("owner key numbers must be finite")


def _is_scalar(value: object) -> bool:
    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, int):
        return -9_007_199_254_740_991 <= value <= 9_007_199_254_740_991
    return isinstance(value, float) and math.isfinite(value)


def _canonical_key(owner_key: OwnerKey) -> str:
    _validate_owner_key(owner_key)
    return json.dumps(owner_key, ensure_ascii=False, separators=(",", ":"))


def _encode_scalar(value: Scalar) -> bytes:
    if value is None:
        return b"n\n"
    if isinstance(value, bool):
        return b"b1\n" if value else b"b0\n"
    if isinstance(value, int):
        return f"i{value}\n".encode()
    if isinstance(value, float):
        return f"f{struct.pack('>d', value).hex()}\n".encode()
    encoded = value.encode("utf-8")
    return f"s{len(encoded)}:".encode() + encoded + b"\n"


def _encode_array(values: tuple[Scalar | tuple[Scalar, ...], ...]) -> bytes:
    encoded = [f"a{len(values)}\n".encode()]
    for value in values:
        encoded.append(_encode_array(value) if isinstance(value, tuple) else _encode_scalar(value))
    return b"".join(encoded)


def _canonical_member(member: ExpiryOwner) -> bytes:
    return _encode_array((member.resource_kind, member.owner_key))


def _canonical_batch_bytes(
    *,
    resource_class: ResourceClass,
    policy_revision: str,
    cutoff: datetime,
    ttl_seconds: int,
    members: tuple[ExpiryOwner, ...],
) -> bytes:
    normalized_cutoff = (
        cutoff.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )
    return b"".join(
        (
            b"evidence-expiry-batch-v1\n",
            _encode_scalar(resource_class.value),
            _encode_scalar(policy_revision),
            _encode_scalar(normalized_cutoff),
            _encode_scalar(ttl_seconds),
            b"a" + str(len(members)).encode() + b"\n",
            *(_canonical_member(member) for member in members),
        )
    )


@dataclass(frozen=True, slots=True)
class TraceSummary:
    trace_id: str
    state: TraceDetailState

    def __post_init__(self) -> None:
        if len(self.trace_id) != 32 or any(
            character not in "0123456789abcdef" for character in self.trace_id
        ):
            raise ValueError("trace_id must be 32 lower-case hex")


@dataclass(frozen=True, slots=True)
class ExpiryBatch:
    batch_identity: str
    resource_class: ResourceClass
    policy_revision: str
    cutoff: datetime
    ttl_seconds: int
    members: tuple[ExpiryOwner, ...]

    @classmethod
    def create(
        cls,
        *,
        resource_class: ResourceClass,
        policy_revision: str,
        cutoff: datetime,
        ttl_seconds: int,
        members: tuple[ExpiryOwner, ...],
    ) -> ExpiryBatch:
        if resource_class is ResourceClass.ACCEPTED_PROVENANCE:
            raise ValueError("accepted provenance cannot expire")
        if not policy_revision:
            raise ValueError("policy_revision must be nonempty")
        _require_aware(cutoff, "cutoff")
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or ttl_seconds < 0:
            raise ValueError("ttl_seconds must be a nonnegative integer")
        if len(members) > 1000:
            raise ValueError("expiry batch cannot exceed 1000 members")
        for member in members:
            _validate_expiry_owner(resource_class, member)
        canonical = sorted((_canonical_member(member), member) for member in members)
        if any(left[0] == right[0] for left, right in zip(canonical, canonical[1:], strict=False)):
            raise ValueError("expiry batch members must be unique")
        ordered = tuple(member for _, member in canonical)
        normalized_cutoff = cutoff.astimezone(UTC)
        encoded = _canonical_batch_bytes(
            resource_class=resource_class,
            policy_revision=policy_revision,
            cutoff=normalized_cutoff,
            ttl_seconds=ttl_seconds,
            members=ordered,
        )
        return cls(
            batch_identity=hashlib.sha256(encoded).hexdigest(),
            resource_class=resource_class,
            policy_revision=policy_revision,
            cutoff=normalized_cutoff,
            ttl_seconds=ttl_seconds,
            members=ordered,
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


@dataclass(frozen=True, slots=True)
class DeliveryDeletionBatch:
    batch_identity: str
    policy_revision: str
    cutoff: datetime
    ttl_seconds: int
    delivery_ids: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        policy_revision: str,
        cutoff: datetime,
        ttl_seconds: int,
        delivery_ids: tuple[str, ...],
    ) -> DeliveryDeletionBatch:
        if not policy_revision:
            raise ValueError("policy_revision must be nonempty")
        _require_aware(cutoff, "cutoff")
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or ttl_seconds < 0:
            raise ValueError("ttl_seconds must be a nonnegative integer")
        if len(delivery_ids) > 1000:
            raise ValueError("Delivery deletion batch cannot exceed 1000 members")
        if any(not isinstance(delivery_id, str) or not delivery_id for delivery_id in delivery_ids):
            raise ValueError("delivery_id must be nonempty")
        ordered = tuple(sorted(delivery_ids))
        if any(left == right for left, right in zip(ordered, ordered[1:], strict=False)):
            raise ValueError("Delivery deletion batch members must be unique")
        normalized_cutoff = cutoff.astimezone(UTC)
        cutoff_text = normalized_cutoff.isoformat(timespec="microseconds").replace("+00:00", "Z")
        encoded = b"".join(
            (
                b"evidence-delivery-deletion-batch-v1\n",
                _encode_scalar(policy_revision),
                _encode_scalar(cutoff_text),
                _encode_scalar(ttl_seconds),
                _encode_array(ordered),
            )
        )
        return cls(
            batch_identity=hashlib.sha256(encoded).hexdigest(),
            policy_revision=policy_revision,
            cutoff=normalized_cutoff,
            ttl_seconds=ttl_seconds,
            delivery_ids=ordered,
        )


@dataclass(frozen=True, slots=True)
class DeliveryDeletionResult:
    batch_identity: str
    selected: int
    deleted: int
    already_deleted: int

    def __post_init__(self) -> None:
        if len(self.batch_identity) != 64 or any(
            character not in "0123456789abcdef" for character in self.batch_identity
        ):
            raise ValueError("batch_identity must be lower-case SHA-256 hex")
        if min(self.selected, self.deleted, self.already_deleted) < 0:
            raise ValueError("Delivery deletion counts must be nonnegative")
        if self.deleted + self.already_deleted != self.selected:
            raise ValueError("Delivery deletion result must exactly partition selected Deliveries")


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
        resource_kind: str,
        owner_key: OwnerKey,
        snapshot_id: str,
    ) -> ExpiryRecord | None: ...

    async def summarize_traces(self, *, snapshot_id: str) -> tuple[TraceSummary, ...]: ...


@runtime_checkable
class ExpiryMaintenance(Protocol):
    async def plan_expiry(
        self,
        *,
        resource_class: ResourceClass,
        policy_revision: str,
        cutoff: datetime,
        ttl_seconds: int,
        limit: int,
    ) -> ExpiryBatch: ...

    async def apply_expiry(self, *, batch: ExpiryBatch, clock_now: datetime) -> ExpiryResult: ...


@runtime_checkable
class DeliveryRetentionMaintenance(ExpiryMaintenance, Protocol):
    async def plan_delivery_deletion(
        self,
        *,
        policy_revision: str,
        cutoff: datetime,
        ttl_seconds: int,
        limit: int,
    ) -> DeliveryDeletionBatch: ...

    async def apply_delivery_deletion(
        self, *, batch: DeliveryDeletionBatch, clock_now: datetime
    ) -> DeliveryDeletionResult: ...


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
