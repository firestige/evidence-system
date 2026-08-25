from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceRequest,
    ExportLogsServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, InstrumentationScope, KeyValue
from opentelemetry.proto.logs.v1.logs_pb2 import LogRecord, ResourceLogs, ScopeLogs
from opentelemetry.proto.resource.v1.resource_pb2 import Resource

from wsr_evidence.admission.service import AdmissionService, Disposition
from wsr_evidence.app import create_app
from wsr_evidence.model import ProjectionEffect
from wsr_evidence.transport.otlp import OtlpIngestor, decode_logs_request


def _kv(name: str, value: str | float) -> KeyValue:
    any_value = (
        AnyValue(string_value=value) if isinstance(value, str) else AnyValue(double_value=value)
    )
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
