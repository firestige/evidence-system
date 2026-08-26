from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient

from wsr_evidence.admission.service import AdmissionService, Disposition
from wsr_evidence.app import create_app
from wsr_evidence.query.faults import SnapshotError, SnapshotFault
from wsr_evidence.query.postgresql import PostgresQueryReadModel
from wsr_evidence.storage.postgresql import PostgresStorage


def sampling_record(event_id: str) -> dict[str, Any]:
    return {
        "profile_version": "1.0.0",
        "record_type": "event",
        "event_name": "sampling.decision",
        "resource": {"service.name": "dsh", "service.version": "1"},
        "scope": {
            "name": "io.agentops.dsh.observation",
            "version": "1.0.0",
            "schema_url": "https://opentelemetry.io/schemas/1.41.0",
        },
        "attributes": {
            "agentops.event.id": event_id,
            "agentops.sampling.decision": "DROP",
            "agentops.sampling.probability": 0.0,
        },
    }


def span_record(trace_id: str, *, delivery_id: str = "delivery-1") -> dict[str, Any]:
    span_id = "a" * 16
    return {
        "profile_version": "1.0.0",
        "record_type": "span",
        "span_name": "invoke_workflow delivery",
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": "b" * 16,
        "span_kind": "INTERNAL",
        "start_time_unix_nano": "100",
        "end_time_unix_nano": "200",
        "span_flags": 1,
        "span_links": [{"trace_id": "f" * 32, "span_id": "c" * 16, "flags": 1}],
        "span_status": "OK",
        "resource": {"service.name": "dsh", "service.version": "1"},
        "scope": {
            "name": "io.agentops.dsh.observation",
            "version": "1.0.0",
            "schema_url": "https://opentelemetry.io/schemas/1.41.0",
        },
        "attributes": {
            "agentops.delivery.id": delivery_id,
            "agentops.workflow.id": "workflow-1",
            "agentops.workflow.version": "1.0.0",
            "agentops.implementation.id": "implementation-1",
            "agentops.runtime.id": "runtime-1",
            "agentops.manifest.digest": "d" * 64,
            "agentops.workflow.family": "implementation",
        },
    }


async def clear_core(database_url: str) -> None:
    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        await connection.execute("TRUNCATE projection_effects, accepted_records")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_snapshot_cursor_excludes_later_commits_and_is_replay_stable() -> None:
    database_url = os.environ.get("WSR_EVIDENCE_DATABASE_URL")
    if database_url is None:
        pytest.skip("WSR_EVIDENCE_DATABASE_URL is not configured")
    await clear_core(database_url)
    storage = await PostgresStorage.open(database_url)
    query_storage = PostgresQueryReadModel.from_storage(storage)
    admission = AdmissionService(storage)
    now = datetime(2026, 8, 26, 2, 0, tzinfo=UTC)
    try:
        admitted_a = await admission.admit(sampling_record("event-a"))
        admitted_b = await admission.admit(sampling_record("event-b"))
        assert admitted_a.disposition is Disposition.ACCEPTED
        assert admitted_b.disposition is Disposition.ACCEPTED

        first = await query_storage.acquire_snapshot(
            query="FACTS", filters=(), limit=1, clock_now=now
        )
        assert [effect.key for effect in first.resources] == [("sampling.decision", "event-a")]
        assert first.next_cursor is not None

        admitted_c = await admission.admit(sampling_record("event-c"))
        assert admitted_c.disposition is Disposition.ACCEPTED

        second = await query_storage.continue_snapshot(cursor=first.next_cursor, clock_now=now)
        replay = await query_storage.continue_snapshot(cursor=first.next_cursor, clock_now=now)
        assert [effect.key for effect in second.resources] == [("sampling.decision", "event-b")]
        assert replay == second
        assert second.next_cursor is None
    finally:
        await query_storage.close()
        await storage.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_trace_sort_is_node_parent_link_and_delivery_traversal_is_bounded() -> None:
    database_url = os.environ.get("WSR_EVIDENCE_DATABASE_URL")
    if database_url is None:
        pytest.skip("WSR_EVIDENCE_DATABASE_URL is not configured")
    await clear_core(database_url)
    storage = await PostgresStorage.open(database_url)
    query_storage = PostgresQueryReadModel.from_storage(storage)
    admission = AdmissionService(storage)
    now = datetime(2026, 8, 26, 2, 0, tzinfo=UTC)
    try:
        first_trace = "0" * 31 + "1"
        admitted = await admission.admit(span_record(first_trace))
        assert admitted.disposition is Disposition.ACCEPTED
        page = await query_storage.acquire_snapshot(
            query="TRACES", filters=(("trace_id", first_trace),), limit=10, clock_now=now
        )
        assert [effect.kind for effect in page.resources] == [
            "trace_node",
            "trace_parent_edge",
            "trace_link",
        ]

        await query_storage.close()
        query_storage = PostgresQueryReadModel.from_storage(storage)
        for index in range(2, 34):
            trace_id = f"{index:032x}"
            result = await admission.admit(span_record(trace_id))
            assert result.disposition is Disposition.ACCEPTED
        with pytest.raises(SnapshotError) as exceeded:
            await query_storage.acquire_snapshot(
                query="TRACES",
                filters=(("delivery_id", "delivery-1"),),
                limit=200,
                clock_now=now,
            )
        assert exceeded.value.fault is SnapshotFault.BOUND_EXCEEDED
    finally:
        await query_storage.close()
        await storage.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_lifespan_serves_empty_queries_without_leaking_snapshot_capacity() -> None:
    database_url = os.environ.get("WSR_EVIDENCE_DATABASE_URL")
    if database_url is None:
        pytest.skip("WSR_EVIDENCE_DATABASE_URL is not configured")
    await clear_core(database_url)
    app = create_app(database_url=database_url)
    transport = ASGITransport(app=app)

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://evidence.test") as client,
    ):
        responses = [await client.get("/v1/evidence/facts") for _ in range(5)]

    assert all(response.status_code == 200 for response in responses)
    assert all(response.json()["items"] == [] for response in responses)
    assert len({response.json()["snapshot"] for response in responses}) == 5
