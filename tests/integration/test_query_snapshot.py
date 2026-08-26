from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient

from wsr_evidence.admission.service import AdmissionService, Disposition
from wsr_evidence.app import create_app
from wsr_evidence.query.faults import SnapshotError, SnapshotFault
from wsr_evidence.query.postgresql import PostgresQueryReadModel
from wsr_evidence.query.service import QueryService
from wsr_evidence.retention.postgresql import PostgresRetentionMaintenance
from wsr_evidence.storage.postgresql import PostgresStorage
from wsr_evidence.storage.read_model import (
    ExpiryBatch,
    ExpiryOwner,
    ResourceClass,
    RetentionPolicy,
)


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
        await connection.execute(
            "TRUNCATE retention_expiry_markers, projection_effects, accepted_records"
        )


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
async def test_raw_debug_scrub_does_not_change_projection_filters() -> None:
    database_url = os.environ.get("WSR_EVIDENCE_DATABASE_URL")
    if database_url is None:
        pytest.skip("WSR_EVIDENCE_DATABASE_URL is not configured")
    await clear_core(database_url)
    storage = await PostgresStorage.open(database_url)
    query_storage = PostgresQueryReadModel.from_storage(storage)
    admission = AdmissionService(storage)
    trace_id = "0" * 31 + "1"
    try:
        admitted = await admission.admit(span_record(trace_id))
        assert admitted.disposition is Disposition.ACCEPTED
        async with await psycopg.AsyncConnection.connect(database_url) as connection:
            await connection.execute("UPDATE accepted_records SET logical_record = '{}'::jsonb")

        service = QueryService(query_storage)
        facts = await service.facts({"trace_id": trace_id, "limit": "10"})
        assert [item["kind"] for item in facts["items"]] == ["DELIVERY_ROOT_BINDING"]
        assert {field["field"] for field in facts["items"][0]["fields"]} == {
            "C01",
            "C06",
            "C07",
            "C08",
        }

        traces = await service.traces({"delivery_id": "delivery-1", "limit": "10"})
        assert [item["kind"] for item in traces["items"]] == ["NODE", "PARENT_EDGE", "LINK"]
    finally:
        await query_storage.close()
        await storage.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retention_lifecycles_are_independent_idempotent_and_queryable() -> None:
    database_url = os.environ.get("WSR_EVIDENCE_DATABASE_URL")
    if database_url is None:
        pytest.skip("WSR_EVIDENCE_DATABASE_URL is not configured")
    await clear_core(database_url)
    storage = await PostgresStorage.open(database_url)
    query_storage = PostgresQueryReadModel.from_storage(storage)
    maintenance = PostgresRetentionMaintenance.from_storage(storage)
    admission = AdmissionService(storage)
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    old = datetime(2025, 1, 1, tzinfo=UTC)
    trace_id = "0" * 31 + "1"
    try:
        assert (
            await admission.admit(sampling_record("sampling-old"))
        ).disposition is Disposition.ACCEPTED
        assert (await admission.admit(span_record(trace_id))).disposition is Disposition.ACCEPTED
        async with await psycopg.AsyncConnection.connect(database_url) as connection:
            await connection.execute("UPDATE accepted_records SET accepted_at = %s", (old,))
            await connection.execute("UPDATE projection_effects SET recorded_at = %s", (old,))
            await connection.execute(
                """
                INSERT INTO accepted_records
                    (identity_kind, identity_key, canonical_digest, profile_version,
                     family_schema, logical_record, accepted_at)
                VALUES ('event', '["event","accepted-without-projection"]', %s,
                        '1.0.0', NULL, '{}'::jsonb, %s)
                """,
                ("f" * 64, old),
            )

        raw = await maintenance.plan_expiry(
            resource_class=ResourceClass.RAW_DEBUG,
            policy_revision="1.0.0",
            cutoff=now,
            ttl_seconds=0,
            limit=10,
        )
        first_raw = await maintenance.apply_expiry(batch=raw, clock_now=now)
        repeated_raw = await maintenance.apply_expiry(batch=raw, clock_now=now)
        assert first_raw.expired == 2
        assert repeated_raw.already_expired == 2
        assert all(
            member.owner_key != ("event", "accepted-without-projection") for member in raw.members
        )

        async with await psycopg.AsyncConnection.connect(database_url) as connection:
            rows = await connection.execute(
                "SELECT logical_record, canonical_digest FROM accepted_records"
            )
            accepted = await rows.fetchall()
        assert all(logical == {} for logical, _ in accepted)
        assert all(len(digest) == 64 for _, digest in accepted)
        assert (
            await admission.admit(sampling_record("sampling-old"))
        ).disposition is Disposition.DUPLICATE
        conflicting = sampling_record("sampling-old")
        conflicting["attributes"]["agentops.sampling.probability"] = 1.0
        assert (await admission.admit(conflicting)).disposition is Disposition.CONFLICT

        active_facts = await QueryService(query_storage).facts({"event_name": "sampling.decision"})
        assert active_facts["items"][0]["truth"]["expiry"] == "ACTIVE"

        pre_expiry_snapshot = await query_storage.acquire_snapshot(
            query="FACTS",
            filters=(("event_name", "sampling.decision"),),
            limit=10,
            clock_now=now,
        )
        assert (
            pre_expiry_snapshot.resources[0].payload["attributes"]["agentops.sampling.decision"]
            == "DROP"
        )

        factual = await maintenance.plan_expiry(
            resource_class=ResourceClass.FACTUAL_PROJECTION,
            policy_revision="1.0.0",
            cutoff=now,
            ttl_seconds=31_536_000,
            limit=10,
        )
        concurrent_results = await asyncio.gather(
            maintenance.apply_expiry(batch=factual, clock_now=now),
            maintenance.apply_expiry(batch=factual, clock_now=now),
        )
        assert sorted(result.expired for result in concurrent_results) == [
            0,
            len(factual.members),
        ]
        assert sorted(result.already_expired for result in concurrent_results) == [
            0,
            len(factual.members),
        ]
        assert (
            await query_storage.read_expiry(
                resource_class=ResourceClass.FACTUAL_PROJECTION,
                resource_kind="EVENT_CONTRIBUTION",
                owner_key=pre_expiry_snapshot.resources[0].key,
                snapshot_id=pre_expiry_snapshot.snapshot_id,
            )
            is None
        )
        await query_storage.release_snapshot(pre_expiry_snapshot.snapshot_id)
        expired_facts = await QueryService(
            query_storage,
            retention_policy=RetentionPolicy(factual_projection_ttl=timedelta(days=30)),
        ).facts({"event_name": "sampling.decision"})
        assert expired_facts["items"][0]["fields"] == []
        assert expired_facts["items"][0]["truth"]["expiry"] == "EXPIRED"
        assert expired_facts["items"][0]["truth"]["availability"] == "UNAVAILABLE"
        assert expired_facts["items"][0]["truth"]["expires_at"] == "2026-01-01T00:00:00.000000Z"

        active_trace = await QueryService(query_storage).traces({"delivery_id": "delivery-1"})
        assert active_trace["trace_state"] == "AVAILABLE"
        trace = await maintenance.plan_expiry(
            resource_class=ResourceClass.TRACE_DETAIL,
            policy_revision="1.0.0",
            cutoff=now,
            ttl_seconds=2_592_000,
            limit=10,
        )
        partial_trace = ExpiryBatch.create(
            resource_class=ResourceClass.TRACE_DETAIL,
            policy_revision="1.0.0",
            cutoff=now,
            ttl_seconds=2_592_000,
            members=(trace.members[0],),
        )
        await maintenance.apply_expiry(batch=partial_trace, clock_now=now)
        partially_expired_trace = await QueryService(query_storage).traces(
            {"delivery_id": "delivery-1"}
        )
        assert partially_expired_trace["trace_state"] == "PARTIAL"
        assert partially_expired_trace["trace_summaries"] == [
            {"trace_id": trace_id, "state": "PARTIAL"}
        ]
        await maintenance.apply_expiry(batch=trace, clock_now=now)
        expired_trace = await QueryService(query_storage).traces({"delivery_id": "delivery-1"})
        assert expired_trace["trace_state"] == "EXPIRED"
        assert expired_trace["items"] == []

        await query_storage.close()
        query_storage = PostgresQueryReadModel.from_storage(storage)
        restarted_trace = await QueryService(query_storage).traces({"delivery_id": "delivery-1"})
        restarted_fact = await QueryService(query_storage).facts(
            {"event_name": "sampling.decision"}
        )
        assert restarted_trace["trace_state"] == "EXPIRED"
        assert restarted_fact["items"][0]["truth"]["expiry"] == "EXPIRED"

        with pytest.raises(ValueError, match="accepted provenance"):
            await maintenance.plan_expiry(
                resource_class=ResourceClass.ACCEPTED_PROVENANCE,
                policy_revision="1.0.0",
                cutoff=now,
                ttl_seconds=0,
                limit=10,
            )
    finally:
        await query_storage.close()
        await storage.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retention_batch_failure_rolls_back_scrub_and_tombstone() -> None:
    database_url = os.environ.get("WSR_EVIDENCE_DATABASE_URL")
    if database_url is None:
        pytest.skip("WSR_EVIDENCE_DATABASE_URL is not configured")
    await clear_core(database_url)
    storage = await PostgresStorage.open(database_url)
    maintenance = PostgresRetentionMaintenance.from_storage(storage)
    admission = AdmissionService(storage)
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    try:
        assert (
            await admission.admit(sampling_record("event-a"))
        ).disposition is Disposition.ACCEPTED
        batch = ExpiryBatch.create(
            resource_class=ResourceClass.RAW_DEBUG,
            policy_revision="1.0.0",
            cutoff=now,
            ttl_seconds=0,
            members=(
                ExpiryOwner(resource_kind="RAW_DEBUG", owner_key=("event", "event-a")),
                ExpiryOwner(resource_kind="RAW_DEBUG", owner_key=("event", "zz-missing")),
            ),
        )

        with pytest.raises(RuntimeError, match="disappeared"):
            await maintenance.apply_expiry(batch=batch, clock_now=now)

        async with await psycopg.AsyncConnection.connect(database_url) as connection:
            raw = await connection.execute(
                "SELECT logical_record FROM accepted_records WHERE identity_key = %s",
                ('["event","event-a"]',),
            )
            marker = await connection.execute("SELECT count(*) FROM retention_expiry_markers")
            raw_row = await raw.fetchone()
            marker_row = await marker.fetchone()
        assert raw_row is not None and raw_row[0] != {}
        assert marker_row == (0,)
    finally:
        await storage.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_equal_owner_keys_expire_independently_by_public_resource_kind() -> None:
    database_url = os.environ.get("WSR_EVIDENCE_DATABASE_URL")
    if database_url is None:
        pytest.skip("WSR_EVIDENCE_DATABASE_URL is not configured")
    await clear_core(database_url)
    storage = await PostgresStorage.open(database_url)
    query_storage = PostgresQueryReadModel.from_storage(storage)
    maintenance = PostgresRetentionMaintenance.from_storage(storage)
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    owner_key = ("same", "key")
    key_json = '["same","key"]'
    try:
        async with await psycopg.AsyncConnection.connect(database_url) as connection:
            await connection.execute(
                """
                INSERT INTO accepted_records
                    (identity_kind, identity_key, canonical_digest, profile_version,
                     family_schema, logical_record, accepted_at)
                VALUES
                    ('event', '["event","assertion"]', %s, '1.0.0',
                     'system-design@1', '{}'::jsonb, %s),
                    ('event', '["event","lineage"]', %s, '1.0.0',
                     'system-design@1', '{}'::jsonb, %s)
                """,
                ("a" * 64, now, "b" * 64, now),
            )
            await connection.execute(
                """
                INSERT INTO projection_effects
                    (effect_kind, effect_key, payload, source_identity_kind,
                     source_identity_key, recorded_at)
                VALUES
                    ('finding_assertion', %s, '{}'::jsonb,
                     'event', '["event","assertion"]', %s),
                    ('role_lineage', %s, '{}'::jsonb,
                     'event', '["event","lineage"]', %s)
                """,
                (key_json, now, key_json, now),
            )

        planned = await maintenance.plan_expiry(
            resource_class=ResourceClass.FACTUAL_PROJECTION,
            policy_revision="1.0.0",
            cutoff=now,
            ttl_seconds=31_536_000,
            limit=10,
        )
        assert {(member.resource_kind, member.owner_key) for member in planned.members} == {
            ("FINDING_ASSERTION", owner_key),
            ("ROLE_LINEAGE", owner_key),
        }

        assertion_only = ExpiryBatch.create(
            resource_class=ResourceClass.FACTUAL_PROJECTION,
            policy_revision="1.0.0",
            cutoff=now,
            ttl_seconds=31_536_000,
            members=(ExpiryOwner(resource_kind="FINDING_ASSERTION", owner_key=owner_key),),
        )
        await maintenance.apply_expiry(batch=assertion_only, clock_now=now)
        snapshot = await query_storage.acquire_snapshot(
            query="FACTS", filters=(), limit=10, clock_now=now
        )
        assertion_expiry = await query_storage.read_expiry(
            resource_class=ResourceClass.FACTUAL_PROJECTION,
            resource_kind="FINDING_ASSERTION",
            owner_key=owner_key,
            snapshot_id=snapshot.snapshot_id,
        )
        lineage_expiry = await query_storage.read_expiry(
            resource_class=ResourceClass.FACTUAL_PROJECTION,
            resource_kind="ROLE_LINEAGE",
            owner_key=owner_key,
            snapshot_id=snapshot.snapshot_id,
        )
        assert assertion_expiry is not None
        assert lineage_expiry is None
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
