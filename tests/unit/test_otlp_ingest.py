from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from hashlib import sha256
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceRequest,
    ExportLogsServiceResponse,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, InstrumentationScope, KeyValue
from opentelemetry.proto.logs.v1.logs_pb2 import LogRecord, ResourceLogs, ScopeLogs
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span, Status

from wsr_evidence.admission.service import AdmissionService, Disposition
from wsr_evidence.admission.validation import ValidationError, canonical_bytes, validate_record
from wsr_evidence.app import create_app
from wsr_evidence.model import ProjectionEffect
from wsr_evidence.transport.otlp import OtlpIngestor, decode_logs_request, decode_traces_request


def _kv(name: str, value: str | int | float) -> KeyValue:
    if isinstance(value, str):
        any_value = AnyValue(string_value=value)
    elif isinstance(value, int):
        any_value = AnyValue(int_value=value)
    else:
        any_value = AnyValue(double_value=value)
    return KeyValue(key=name, value=any_value)


def log_request(*records: LogRecord) -> bytes:
    request = ExportLogsServiceRequest(
        resource_logs=[
            ResourceLogs(
                resource=Resource(
                    attributes=[_kv("service.name", "dsh"), _kv("service.version", "1")]
                ),
                scope_logs=[
                    ScopeLogs(
                        scope=InstrumentationScope(
                            name="io.agentops.dsh.observation", version="1.0.0"
                        ),
                        schema_url="https://opentelemetry.io/schemas/1.41.0",
                        log_records=list(records),
                    )
                ],
            )
        ]
    )
    return request.SerializeToString()


def sampling_record(event_id: str, *, unknown: bool = False) -> LogRecord:
    attributes = [
        _kv("agentops.event.id", event_id),
        _kv("agentops.sampling.decision", "DROP"),
        _kv("agentops.sampling.probability", 0.0),
    ]
    if unknown:
        attributes.append(_kv("agentops.invalid.reason", "bad"))
    return LogRecord(event_name="sampling.decision", attributes=attributes)


def task_binding_log() -> LogRecord:
    roles: list[dict[str, str]] = []
    projection = canonical_bytes(
        {
            "schema_version": "execution.delivery-manifest-projection@1.0.0",
            "delivery_id": "delivery-1",
            "task_id": "task-1",
            "manifest_digest": "a" * 64,
            "workflow": {
                "package_name": "implementation",
                "exact_package_version": "2.0.0",
                "package_digest": f"sha256:{'b' * 64}",
                "workflow_id": "workflow.implementation",
                "workflow_version": "2.0.0",
                "snapshot_id": "snapshot.implementation.2",
                "snapshot_digest": f"sha256:{'c' * 64}",
            },
            "repository_model_bindings": {
                "document_state": "ABSENT",
                "resolved_map_digest": f"sha256:{sha256(canonical_bytes(roles)).hexdigest()}",
            },
            "roles": roles,
        }
    ).decode()
    return LogRecord(
        event_name="task.binding",
        attributes=[
            _kv("agentops.delivery.id", "delivery-1"),
            _kv("agentops.task.id", "task-1"),
            _kv("agentops.manifest.digest", "a" * 64),
            _kv(
                "agentops.event.id",
                f"task-binding-{sha256(b'delivery-1').hexdigest()[:24]}",
            ),
            _kv("agentops.task.display_name", "Token tuning"),
            _kv("agentops.delivery.manifest_projection", projection),
            _kv(
                "agentops.delivery.manifest_projection_digest",
                sha256(projection.encode()).hexdigest(),
            ),
        ],
    )


def mixed_profile_log_request() -> bytes:
    resource = Resource(attributes=[_kv("service.name", "dsh"), _kv("service.version", "1")])
    request = ExportLogsServiceRequest(
        resource_logs=[
            ResourceLogs(
                resource=resource,
                scope_logs=[
                    ScopeLogs(
                        scope=InstrumentationScope(
                            name="io.agentops.dsh.observation", version="1.0.0"
                        ),
                        schema_url="https://opentelemetry.io/schemas/1.41.0",
                        log_records=[sampling_record("event-1")],
                    ),
                    ScopeLogs(
                        scope=InstrumentationScope(
                            name="io.agentops.dsh.observation", version="2.0.0"
                        ),
                        schema_url="https://opentelemetry.io/schemas/1.41.0",
                        log_records=[task_binding_log()],
                    ),
                ],
            )
        ]
    )
    return request.SerializeToString()


def usage_record(index: int) -> LogRecord:
    return LogRecord(
        event_name="usage",
        attributes=[
            _kv("agentops.event.id", f"usage-{index}"),
            _kv("agentops.workflow.family", "implementation"),
            _kv("agentops.family.schema", "implementation@1"),
            _kv("agentops.summary.state", "FINAL"),
            _kv("agentops.usage.kind", "native_credit"),
            _kv("agentops.usage.unit", "credit"),
            _kv("agentops.usage.source", "runtime"),
            _kv("agentops.usage.source.id", f"runtime-{index}"),
            _kv("agentops.usage.value", 0),
        ],
    )


def trace_request(*, status_code: int = Status.STATUS_CODE_UNSET) -> bytes:
    root = Span(
        trace_id=b"1" * 16,
        span_id=b"1" * 8,
        name="invoke_workflow delivery-1",
        kind=Span.SPAN_KIND_INTERNAL,
        start_time_unix_nano=100,
        end_time_unix_nano=200,
        flags=1,
        status=Status(code=status_code),
        attributes=[
            _kv("agentops.delivery.id", "delivery-1"),
            _kv("agentops.workflow.id", "workflow-1"),
            _kv("agentops.workflow.version", "1"),
            _kv("agentops.implementation.id", "implementation-1"),
            _kv("agentops.runtime.id", "runtime-1"),
            _kv("agentops.manifest.digest", "a" * 64),
            _kv("agentops.workflow.family", "implementation"),
        ],
    )
    return ExportTraceServiceRequest(
        resource_spans=[
            ResourceSpans(
                resource=Resource(
                    attributes=[_kv("service.name", "dsh"), _kv("service.version", "1")]
                ),
                scope_spans=[
                    ScopeSpans(
                        scope=InstrumentationScope(
                            name="io.agentops.dsh.observation", version="1.0.0"
                        ),
                        schema_url="https://opentelemetry.io/schemas/1.41.0",
                        spans=[root],
                    )
                ],
            )
        ]
    ).SerializeToString()


class MemoryTransaction:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state

    async def claim_identity(self, record: Any) -> Disposition:
        digest = self.state["identities"].get(record.identity)
        if digest is None:
            self.state["identities"][record.identity] = record.digest
            return Disposition.ACCEPTED
        return Disposition.DUPLICATE if digest == record.digest else Disposition.CONFLICT

    async def apply_effects(self, effects: tuple[ProjectionEffect, ...]) -> None:
        self.state["effects"].extend(effects)


class MemoryStorage:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {"identities": {}, "effects": []}

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[MemoryTransaction]:
        working = deepcopy(self.state)
        yield MemoryTransaction(working)
        self.state = working


def test_official_otlp_log_protobuf_decodes_to_closed_logical_record() -> None:
    records = decode_logs_request(log_request(sampling_record("event-1")))

    assert len(records) == 1
    assert records[0]["record_type"] == "event"
    assert records[0]["event_name"] == "sampling.decision"
    assert records[0]["attributes"]["agentops.sampling.probability"] == 0.0


@pytest.mark.asyncio
async def test_profile_two_task_protobuf_projects_the_exact_authority_slice() -> None:
    records = decode_logs_request(mixed_profile_log_request())
    task = records[1]

    validated = validate_record(task)
    effects = AdmissionService.project(validated)

    assert task["profile_version"] == "2.0.0"
    assert [effect.kind for effect in effects] == [
        "task_declaration",
        "delivery_task_membership",
        "delivery_task_guard",
        "task_display_name",
        "delivery_manifest",
    ]


@pytest.mark.asyncio
async def test_mixed_profile_request_is_rejected_before_any_record_lands() -> None:
    storage = MemoryStorage()
    outcome = await OtlpIngestor(AdmissionService(storage)).ingest_logs(mixed_profile_log_request())

    assert outcome.http_status == 400
    assert outcome.rejected_items == 2
    assert storage.state["identities"] == {}


def test_official_otlp_trace_protobuf_preserves_native_identity_fields() -> None:
    records = decode_traces_request(trace_request())

    assert records[0]["record_type"] == "span"
    assert records[0]["trace_id"] == "31" * 16
    assert records[0]["span_id"] == "31" * 8
    assert records[0]["span_flags"] == 1
    assert records[0]["span_status"] == "UNSET"


def test_unknown_native_trace_enum_becomes_an_isolated_record_rejection() -> None:
    records = decode_traces_request(trace_request(status_code=99))

    with pytest.raises(ValidationError, match="unknown record field"):
        validate_record(records[0])


@pytest.mark.asyncio
async def test_mixed_batch_isolates_siblings_and_returns_only_aggregate_counts() -> None:
    storage = MemoryStorage()
    ingestor = OtlpIngestor(AdmissionService(storage))

    outcome = await ingestor.ingest_logs(
        log_request(sampling_record("event-good"), sampling_record("event-bad", unknown=True))
    )

    assert outcome.http_status == 200
    assert outcome.rejected_items == 1
    assert outcome.dispositions is None
    assert len(storage.state["identities"]) == 1


@pytest.mark.asyncio
async def test_content_bearing_log_record_isolated_from_valid_sibling() -> None:
    storage = MemoryStorage()
    ingestor = OtlpIngestor(AdmissionService(storage))
    invalid = sampling_record("event-body")
    invalid.body.string_value = "prohibited body"

    outcome = await ingestor.ingest_logs(log_request(sampling_record("event-good"), invalid))

    assert outcome.http_status == 200
    assert outcome.rejected_items == 1
    assert len(storage.state["identities"]) == 1


@pytest.mark.asyncio
async def test_batch_cardinality_budget_rejects_before_any_record_lands() -> None:
    storage = MemoryStorage()
    ingestor = OtlpIngestor(AdmissionService(storage))

    outcome = await ingestor.ingest_logs(log_request(*(usage_record(i) for i in range(257))))

    assert outcome.http_status == 400
    assert outcome.rejected_items == 257
    assert storage.state["identities"] == {}


@pytest.mark.asyncio
async def test_http_otlp_response_is_standard_aggregate_protobuf_without_auth() -> None:
    storage = MemoryStorage()
    app = create_app(otlp_ingestor=OtlpIngestor(AdmissionService(storage)))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://evidence.test") as client:
        response = await client.post(
            "/v1/logs",
            content=log_request(
                sampling_record("event-good"), sampling_record("event-bad", unknown=True)
            ),
            headers={"content-type": "application/x-protobuf"},
        )

    decoded = ExportLogsServiceResponse.FromString(response.content)
    assert response.status_code == 200
    assert decoded.partial_success.rejected_log_records == 1
    assert "www-authenticate" not in response.headers
