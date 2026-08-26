"""Contract-bound shaping for the Evidence read-only API candidate."""

# ruff: noqa: SIM905 -- split registries preserve visible C/I/S numbering parity.

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, cast

from wsr_evidence.clock import Clock, SystemClock
from wsr_evidence.query.faults import SnapshotError, SnapshotFault
from wsr_evidence.query.model import QueryEffect, SnapshotReleaser
from wsr_evidence.storage.read_model import (
    DEFAULT_FACTUAL_PROJECTION_TTL,
    DEFAULT_PAGE_LIMIT,
    DEFAULT_TRACE_DETAIL_TTL,
    MAX_PAGE_LIMIT,
    Availability,
    Completeness,
    ExpiryRecord,
    ExpiryState,
    QueryExpiryReadModel,
    ResourceClass,
    SnapshotPage,
    TruthState,
)

WAVE6_INPUT_MANIFEST_SHA256 = "2be7eac71854b0c37abec240e63a8ec4f97be44ee7dd9990a2714eb106b72a9d"

FACT_KINDS = {
    "factual_contribution": "EVENT_CONTRIBUTION",
    "finding_assertion": "FINDING_ASSERTION",
    "finding_target": "FINDING_TARGET",
    "finding_status": "FINDING_STATUS",
    "finding_fix": "FINDING_FIX",
    "finding_recheck": "FINDING_RECHECK",
    "role_lineage": "ROLE_LINEAGE",
    "delivery_root_binding": "DELIVERY_ROOT_BINDING",
    "model_attribution": "MODEL_ATTRIBUTION",
}
TRACE_KINDS = {
    "trace_node": "NODE",
    "trace_parent_edge": "PARENT_EDGE",
    "trace_link": "LINK",
}

EVENT_NAMES = {
    "delivery.summary",
    "implementation.summary",
    "intervention",
    "review.finding",
    "review.summary",
    "role.lineage",
    "sampling.decision",
    "system_design.summary",
    "test.summary",
    "usage",
}
TRACE_ID = re.compile(r"[0-9a-f]{32}").fullmatch
CURSOR = re.compile(r"[A-Za-z0-9_-]{43}").fullmatch

FIELD_IDS = {
    **{
        name: f"C{index:02d}"
        for index, name in enumerate(
            (
                "agentops.delivery.id agentops.task.id agentops.workflow.id "
                "agentops.workflow.version agentops.implementation.id agentops.runtime.id "
                "agentops.manifest.digest agentops.workflow.family agentops.event.id "
                "agentops.delivery.outcome agentops.summary.state agentops.review.id "
                "agentops.review.lens agentops.review.scope agentops.review.severity "
                "agentops.review.total agentops.review.observed.count agentops.finding.id "
                "agentops.finding.status agentops.source.review.id agentops.fix.id "
                "agentops.fix.finding.id agentops.recheck.id agentops.recheck.review.id "
                "agentops.recheck.finding.id agentops.recheck.fix.id agentops.iteration.id "
                "agentops.artifact.id agentops.artifact.digest agentops.role.id "
                "agentops.role.lineage.id agentops.parent.role.id agentops.writer.role.id "
                "agentops.reviewer.role.id agentops.recheck.role.id "
                "agentops.writer.invocation.id agentops.reviewer.invocation.id "
                "agentops.recheck.invocation.id agentops.intervention.kind "
                "agentops.observed.loop.count agentops.observed.intervention.count "
                "agentops.usage.kind agentops.usage.unit agentops.usage.source "
                "agentops.usage.source.id agentops.usage.value "
                "agentops.sampling.decision agentops.sampling.probability "
                "agentops.family.schema agentops.finding.summary "
                "agentops.finding.scope.id agentops.finding.target.kind "
                "agentops.finding.target.id agentops.finding.target.artifact.id "
                "agentops.delivery.elapsed_time_ms agentops.delivery.stage.reached "
                "agentops.model.id"
            ).split(),
            start=1,
        )
    },
    **{
        name: f"I{index:02d}"
        for index, name in enumerate(
            (
                "agentops.test.passed agentops.test.failed agentops.test.skipped "
                "agentops.test.duration.seconds agentops.coverage.dimension "
                "agentops.coverage.covered agentops.coverage.total "
                "agentops.coverage.scope agentops.coverage.tool.id agentops.coverage.format"
            ).split(),
            start=1,
        )
    },
    **{
        name: f"S{index:02d}"
        for index, name in enumerate(
            (
                "agentops.fresh_reader.result agentops.fresh_reader.finding.count "
                "agentops.verification.id agentops.verification.result "
                "agentops.verification.check.passed agentops.verification.check.failed"
            ).split(),
            start=1,
        )
    },
}

VALUE_FIELDS = {
    "C16",
    "C17",
    "C40",
    "C41",
    "C46",
    "C55",
    "I01",
    "I02",
    "I03",
    "I04",
    "I06",
    "I07",
    "S02",
    "S05",
    "S06",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
}


class QueryErrorCode(StrEnum):
    INVALID_FILTER = "INVALID_FILTER"
    INVALID_CURSOR = "INVALID_CURSOR"
    NOT_ACCEPTABLE = "NOT_ACCEPTABLE"
    CURSOR_MISMATCH = "CURSOR_MISMATCH"
    CURSOR_EXPIRED = "CURSOR_EXPIRED"
    QUERY_BOUND_EXCEEDED = "QUERY_BOUND_EXCEEDED"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    ROUTE_NOT_FOUND = "ROUTE_NOT_FOUND"
    QUERY_INTERNAL = "QUERY_INTERNAL"
    QUERY_UNAVAILABLE = "QUERY_UNAVAILABLE"


class QueryError(Exception):
    def __init__(self, code: QueryErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class QueryService:
    def __init__(
        self, read_model: QueryExpiryReadModel[QueryEffect], clock: Clock | None = None
    ) -> None:
        self._read_model = read_model
        self._clock = clock or SystemClock()
        self._cursor_bindings: dict[str, tuple[str, tuple[tuple[str, str], ...], int]] = {}

    async def facts(
        self, parameters: Mapping[str, str] | Sequence[tuple[str, str]]
    ) -> dict[str, Any]:
        normalized, cursor, limit = _normalize(parameters, route="FACTS")
        page = await self._page("FACTS", normalized, cursor, limit)
        try:
            items = []
            for effect in page.resources:
                expiry = await self._read_model.read_expiry(
                    resource_class=ResourceClass.FACTUAL_PROJECTION,
                    owner_key=effect.key,
                    snapshot_id=page.snapshot_id,
                )
                items.append(_fact_resource(effect, expiry=expiry))
            response = _envelope(page, items)
        except Exception:
            await self._release(page.snapshot_id)
            raise
        if cursor is None and page.next_cursor is None:
            await self._release(page.snapshot_id)
        return response

    async def traces(
        self, parameters: Mapping[str, str] | Sequence[tuple[str, str]]
    ) -> dict[str, Any]:
        normalized, cursor, limit = _normalize(parameters, route="TRACES")
        keys = {name for name, _ in normalized}
        if ("trace_id" in keys) == ("delivery_id" in keys):
            raise QueryError(
                QueryErrorCode.INVALID_FILTER,
                "exactly one trace_id or delivery_id is required",
            )
        page = await self._page("TRACES", normalized, cursor, limit)
        try:
            items = []
            expired = False
            for effect in page.resources:
                expiry = await self._read_model.read_expiry(
                    resource_class=ResourceClass.TRACE_DETAIL,
                    owner_key=effect.key,
                    snapshot_id=page.snapshot_id,
                )
                if expiry is not None:
                    expired = True
                    continue
                items.append(_trace_resource(effect))
            response = _envelope(page, items)
            response["trace_state"] = "AVAILABLE" if items else "EXPIRED" if expired else "ABSENT"
            if not items:
                response["next_cursor"] = None
        except Exception:
            await self._release(page.snapshot_id)
            raise
        if cursor is None and page.next_cursor is None:
            await self._release(page.snapshot_id)
        return response

    async def _release(self, snapshot_id: str) -> None:
        if isinstance(self._read_model, SnapshotReleaser):
            await self._read_model.release_snapshot(snapshot_id)

    async def _page(
        self,
        query: str,
        filters: tuple[tuple[str, str], ...],
        cursor: str | None,
        limit: int,
    ) -> SnapshotPage[QueryEffect]:
        now = self._clock.now()
        binding = (query, filters, limit)
        try:
            if cursor is not None:
                if CURSOR(cursor) is None:
                    raise QueryError(QueryErrorCode.INVALID_CURSOR, "cursor is malformed")
                known = self._cursor_bindings.get(cursor)
                if known is not None and known != binding:
                    raise QueryError(
                        QueryErrorCode.CURSOR_MISMATCH,
                        "cursor parameters do not match the first page",
                    )
                page = await self._read_model.continue_snapshot(cursor=cursor, clock_now=now)
            else:
                page = await self._read_model.acquire_snapshot(
                    query=query, filters=filters, limit=limit, clock_now=now
                )
        except QueryError:
            raise
        except SnapshotError as error:
            code = {
                SnapshotFault.INVALID: (
                    QueryErrorCode.INVALID_CURSOR if cursor else QueryErrorCode.INVALID_FILTER
                ),
                SnapshotFault.MISMATCH: QueryErrorCode.CURSOR_MISMATCH,
                SnapshotFault.EXPIRED: QueryErrorCode.CURSOR_EXPIRED,
                SnapshotFault.BOUND_EXCEEDED: QueryErrorCode.QUERY_BOUND_EXCEEDED,
                SnapshotFault.UNAVAILABLE: QueryErrorCode.QUERY_UNAVAILABLE,
            }[error.fault]
            raise QueryError(code, "snapshot request could not be completed") from error
        except Exception as error:
            raise QueryError(QueryErrorCode.QUERY_INTERNAL, "query failed safely") from error
        if page.next_cursor is not None:
            self._cursor_bindings[page.next_cursor] = binding
        return page


def _normalize(
    parameters: Mapping[str, str] | Sequence[tuple[str, str]], *, route: str
) -> tuple[tuple[tuple[str, str], ...], str | None, int]:
    pairs = list(parameters.items()) if isinstance(parameters, Mapping) else list(parameters)
    names = [name for name, _ in pairs]
    if len(names) != len(set(names)) or any(not name or value == "" for name, value in pairs):
        raise QueryError(
            QueryErrorCode.INVALID_FILTER, "query parameters must be unique and nonempty"
        )
    common = {"limit", "cursor"}
    allowed = (
        common
        | {
            "kind",
            "event_name",
            "family_schema",
            "delivery_id",
            "trace_id",
            "recorded_from",
            "recorded_to",
        }
        if route == "FACTS"
        else common | {"delivery_id", "trace_id"}
    )
    if set(names) - allowed:
        raise QueryError(QueryErrorCode.INVALID_FILTER, "unknown query parameter")
    values = dict(pairs)
    if any("," in value or "*" in value for value in values.values()):
        raise QueryError(QueryErrorCode.INVALID_FILTER, "list and wildcard filters are prohibited")
    try:
        limit = int(values.get("limit", DEFAULT_PAGE_LIMIT))
    except ValueError as error:
        raise QueryError(QueryErrorCode.INVALID_FILTER, "limit must be an integer") from error
    if not 1 <= limit <= MAX_PAGE_LIMIT:
        raise QueryError(QueryErrorCode.INVALID_FILTER, "limit is outside [1,200]")
    cursor = values.get("cursor")
    _validate_filter_values(values, route=route)
    normalized = tuple(sorted((name, value) for name, value in pairs if name != "cursor"))
    return normalized, cursor, limit


def _parse_utc(value: str) -> datetime:
    if not value.endswith("Z"):
        raise QueryError(QueryErrorCode.INVALID_FILTER, "recorded bounds must be UTC timestamps")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise QueryError(QueryErrorCode.INVALID_FILTER, "recorded bound is malformed") from error
    if parsed.utcoffset() != timedelta(0):
        raise QueryError(QueryErrorCode.INVALID_FILTER, "recorded bounds must be UTC timestamps")
    return parsed


def _validate_filter_values(values: Mapping[str, str], *, route: str) -> None:
    if route == "FACTS":
        kind = values.get("kind")
        event_name = values.get("event_name")
        if kind is not None and kind not in FACT_KINDS.values():
            raise QueryError(QueryErrorCode.INVALID_FILTER, "unknown fact kind")
        if event_name is not None and event_name not in EVENT_NAMES:
            raise QueryError(QueryErrorCode.INVALID_FILTER, "unknown EventName")
        if event_name is not None and kind not in {None, "EVENT_CONTRIBUTION"}:
            raise QueryError(QueryErrorCode.INVALID_FILTER, "EventName is incompatible with kind")
        family_schema = values.get("family_schema")
        if family_schema is not None and len(family_schema.encode()) > 128:
            raise QueryError(QueryErrorCode.INVALID_FILTER, "family_schema is too long")
        for name in ("delivery_id",):
            value = values.get(name)
            if value is not None and len(value.encode()) > 256:
                raise QueryError(QueryErrorCode.INVALID_FILTER, f"{name} is too long")
        lower = _parse_utc(values["recorded_from"]) if "recorded_from" in values else None
        upper = _parse_utc(values["recorded_to"]) if "recorded_to" in values else None
        if (
            lower is not None
            and upper is not None
            and (lower > upper or upper - lower > timedelta(days=366))
        ):
            raise QueryError(QueryErrorCode.INVALID_FILTER, "recorded interval is invalid")
    delivery_id = values.get("delivery_id")
    if delivery_id is not None and len(delivery_id.encode()) > 256:
        raise QueryError(QueryErrorCode.INVALID_FILTER, "delivery_id is too long")
    trace_id = values.get("trace_id")
    if trace_id is not None and TRACE_ID(trace_id) is None:
        raise QueryError(QueryErrorCode.INVALID_FILTER, "trace_id must be 32 lower-case hex")


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _source(effect: QueryEffect) -> dict[str, str]:
    identity = json.loads(effect.source_identity[1])
    if effect.source_identity[0] == "event":
        return {"kind": "EVENT", "event_id": str(identity[1])}
    return {"kind": "SPAN", "trace_id": str(identity[1]), "span_id": str(identity[2])}


def _resource_id(effect: QueryEffect, kind: str) -> str:
    prefix = "trace" if effect.kind in TRACE_KINDS else "fact"
    source_key = effect.source_identity[1]
    owner_key = json.dumps(effect.key, ensure_ascii=False, separators=(",", ":"))
    return f"{prefix}:{effect.source_identity[0]}:{source_key}:{kind}:{owner_key}"


def _completeness(effect: QueryEffect) -> Completeness | None:
    attributes = effect.logical_record.get("attributes", {})
    value = attributes.get("agentops.summary.state")
    return Completeness(value) if value is not None else None


def _truth(
    effect: QueryEffect, *, trace: bool = False, expiry: ExpiryRecord | None = None
) -> dict[str, str | None]:
    completeness = None if trace else _completeness(effect)
    availability = (
        Availability.UNAVAILABLE
        if expiry is not None or completeness is Completeness.UNAVAILABLE
        else Availability.AVAILABLE
    )
    ttl = DEFAULT_TRACE_DETAIL_TTL if trace else DEFAULT_FACTUAL_PROJECTION_TTL
    truth = TruthState(
        completeness=completeness,
        availability=availability,
        expiry=ExpiryState.EXPIRED if expiry is not None else ExpiryState.ACTIVE,
        expires_at=effect.recorded_at + ttl,
    )
    return {
        "completeness": truth.completeness.value if truth.completeness else None,
        "availability": truth.availability.value,
        "expiry": truth.expiry.value,
        "expires_at": _timestamp(truth.expires_at) if truth.expires_at else None,
    }


def _fields(effect: QueryEffect) -> list[dict[str, Any]]:
    attributes = _owned_attributes(effect)
    completeness = _completeness(effect)
    fields = []
    for name, value in attributes.items():
        field_id = FIELD_IDS.get(name, name if name.startswith("gen_ai.") else None)
        if field_id is None:
            continue
        if (
            completeness in {Completeness.NOT_APPLICABLE, Completeness.UNAVAILABLE}
            and field_id in VALUE_FIELDS
        ):
            continue
        fields.append({"field": field_id, "value": value})
    return sorted(fields, key=lambda field: str(field["field"]))


def _owned_attributes(effect: QueryEffect) -> Mapping[str, Any]:
    source = effect.logical_record.get("attributes", {})
    if effect.kind == "factual_contribution":
        attributes = effect.payload.get("attributes")
        return cast(dict[str, Any], attributes) if isinstance(attributes, dict) else {}
    if effect.kind in {"finding_assertion", "role_lineage"}:
        return effect.payload
    owned = {
        "finding_target": {
            "agentops.finding.id",
            "agentops.finding.scope.id",
            "agentops.finding.target.kind",
            "agentops.finding.target.id",
            "agentops.finding.target.artifact.id",
        },
        "finding_status": {
            "agentops.finding.id",
            "agentops.finding.status",
            "agentops.review.id",
            "agentops.writer.role.id",
            "agentops.writer.invocation.id",
            "agentops.reviewer.role.id",
            "agentops.reviewer.invocation.id",
        },
        "finding_fix": {
            "agentops.finding.id",
            "agentops.fix.id",
            "agentops.fix.finding.id",
            "agentops.review.id",
            "agentops.writer.role.id",
            "agentops.writer.invocation.id",
            "agentops.reviewer.role.id",
            "agentops.reviewer.invocation.id",
        },
        "finding_recheck": {
            "agentops.finding.id",
            "agentops.recheck.id",
            "agentops.recheck.review.id",
            "agentops.recheck.finding.id",
            "agentops.recheck.fix.id",
            "agentops.iteration.id",
        },
        "delivery_root_binding": {
            "agentops.delivery.id",
            "agentops.runtime.id",
            "agentops.manifest.digest",
            "agentops.workflow.family",
        },
        "model_attribution": {
            "gen_ai.provider.name",
            "gen_ai.request.model",
            "agentops.model.id",
            "agentops.role.id",
            "agentops.runtime.id",
        },
    }.get(effect.kind, set())
    return {name: source[name] for name in owned if name in source}


def _compatibility(effect: QueryEffect) -> dict[str, Any]:
    logical = effect.logical_record
    attributes = logical.get("attributes", {})
    raw_event_name = logical.get("event_name") if effect.kind == "factual_contribution" else None
    event_name = raw_event_name if isinstance(raw_event_name, str) else None
    completeness = attributes.get("agentops.summary.state")
    dimensions: list[dict[str, Any]] = []
    coordinate_map = {
        "usage": ("C42", "C43", "C44", "C45"),
        "implementation.summary": ("I05", "I08", "I09", "I10"),
        "test.summary": ("C28", "C29"),
        "review.summary": ("C13", "C14"),
    }
    coordinate_fields = coordinate_map.get(event_name, ()) if event_name is not None else ()
    inverse = {field_id: name for name, field_id in FIELD_IDS.items()}
    for field_id in coordinate_fields:
        name = inverse[field_id]
        if name in attributes:
            dimensions.append({"field": field_id, "value": attributes[name]})
    return {
        "family_schema": effect.family_schema,
        "event_name": event_name,
        "completeness": completeness,
        "dimensions": dimensions,
    }


def _relationships(effect: QueryEffect) -> list[dict[str, Any]]:
    a = effect.logical_record.get("attributes", {})
    if effect.kind == "finding_target":
        return [
            {"kind": "FINDING_TARGET", "from": list(effect.key[:2]), "to": list(effect.key[2:])}
        ]
    if effect.kind == "finding_fix":
        return [{"kind": "FINDING_FIX", "from": list(effect.key[:5]), "to": effect.key[5]}]
    if effect.kind == "finding_recheck":
        return [{"kind": "FINDING_RECHECK", "from": list(effect.key[:5]), "to": effect.key[5]}]
    if effect.kind == "role_lineage":
        return [
            {
                "kind": "ROLE_LINEAGE",
                "from": a.get("agentops.role.id"),
                "to": a.get("agentops.role.lineage.id"),
            }
        ]
    if effect.kind == "delivery_root_binding":
        return [
            {"kind": "DELIVERY_ROOT", "from": effect.key[0], "to": effect.payload["delivery_id"]}
        ]
    if effect.kind == "model_attribution":
        return [
            {"kind": "MODEL_ATTRIBUTION", "from": list(effect.key[4:]), "to": list(effect.key[:4])}
        ]
    return []


def _fact_resource(effect: QueryEffect, *, expiry: ExpiryRecord | None) -> dict[str, Any]:
    kind = FACT_KINDS.get(effect.kind)
    if kind is None:
        raise QueryError(QueryErrorCode.QUERY_INTERNAL, "unsupported factual projection kind")
    return {
        "id": _resource_id(effect, kind),
        "kind": kind,
        "source": _source(effect),
        "recorded_at": _timestamp(effect.recorded_at),
        "provenance": {
            "accepted_digest": effect.accepted_digest,
            "profile_version": effect.profile_version,
            "family_schema": effect.family_schema,
            "owner_key": list(effect.key),
        },
        "compatibility": _compatibility(effect),
        "truth": _truth(effect, expiry=expiry),
        "fields": [] if expiry is not None else _fields(effect),
        "relationships": [] if expiry is not None else _relationships(effect),
    }


def _trace_resource(effect: QueryEffect) -> dict[str, Any]:
    kind = TRACE_KINDS.get(effect.kind)
    if kind is None:
        raise QueryError(QueryErrorCode.QUERY_INTERNAL, "unsupported Trace projection kind")
    trace_id = str(effect.key[0])
    node = None
    edge = None
    if kind == "NODE":
        node = {**effect.payload, "span_id": effect.key[1]}
        attributes = node.pop("attributes", {})
        node["fields"] = [
            {"field": FIELD_IDS.get(name, name), "value": value}
            for name, value in sorted(attributes.items())
            if name in FIELD_IDS or name.startswith("gen_ai.")
        ]
    elif kind == "PARENT_EDGE":
        edge = {
            "from": {"trace_id": trace_id, "span_id": effect.key[1]},
            "to": {"trace_id": trace_id, "span_id": effect.key[2]},
        }
    else:
        edge = {
            "from": {"trace_id": trace_id, "span_id": effect.key[1]},
            "to": {"trace_id": effect.key[2], "span_id": effect.key[3]},
            **{
                name: effect.payload[name]
                for name in ("trace_state", "flags")
                if name in effect.payload
            },
        }
    return {
        "id": _resource_id(effect, kind),
        "trace_id": trace_id,
        "kind": kind,
        "source": _source(effect),
        "recorded_at": _timestamp(effect.recorded_at),
        "truth": _truth(effect, trace=True),
        "node": node,
        "edge": edge,
    }


def _envelope(page: SnapshotPage[QueryEffect], items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "contract": {"name": "evidence.query", "revision": page.contract_revision},
        "observation_profile": "1.0.0",
        "read_model_revision": page.read_model_revision,
        "snapshot": page.snapshot_id,
        "items": items,
        "next_cursor": page.next_cursor,
    }
