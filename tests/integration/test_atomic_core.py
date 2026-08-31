from __future__ import annotations

import asyncio
import os
from copy import deepcopy
from hashlib import sha256
from typing import Any

import psycopg
import pytest

from wsr_evidence.admission.service import AdmissionService, Disposition
from wsr_evidence.admission.validation import canonical_bytes
from wsr_evidence.storage.postgresql import PostgresStorage
from wsr_evidence.storage.read_model import CORE_READ_MODEL_VERSION


def finding_record(*, event_id: str, target_id: str = "artifact-1") -> dict[str, Any]:
    return {
        "profile_version": "1.0.0",
        "record_type": "event",
        "event_name": "review.finding",
        "resource": {"service.name": "dsh", "service.version": "1"},
        "scope": {
            "name": "io.agentops.dsh.observation",
            "version": "1.0.0",
            "schema_url": "https://opentelemetry.io/schemas/1.41.0",
        },
        "attributes": {
            "agentops.event.id": event_id,
            "agentops.workflow.family": "implementation",
            "agentops.family.schema": "implementation@1",
            "agentops.review.id": "review-1",
            "agentops.review.lens": "IMPLEMENTATION_WHITEBOX",
            "agentops.review.scope": "WHOLE_SCOPE",
            "agentops.review.severity": "MAJOR",
            "agentops.finding.id": "finding-1",
            "agentops.finding.status": "OPEN",
            "agentops.source.review.id": "review-1",
            "agentops.artifact.id": "artifact-1",
            "agentops.artifact.digest": "a" * 64,
            "agentops.writer.role.id": "writer",
            "agentops.writer.invocation.id": "writer-invocation-1",
            "agentops.reviewer.role.id": "reviewer",
            "agentops.reviewer.invocation.id": "reviewer-invocation-1",
            "agentops.finding.summary": "The accepted assertion remains factual.",
            "agentops.finding.scope.id": "scope-1",
            "agentops.finding.target.kind": "ARTIFACT",
            "agentops.finding.target.id": target_id,
        },
    }


def task_binding_record(
    *, task_id: str, delivery_id: str, event_id: str, display_name: str | None
) -> dict[str, Any]:
    del event_id
    manifest_digest = sha256(f"{delivery_id}:{task_id}".encode()).hexdigest()
    roles: list[dict[str, str]] = []
    projection = canonical_bytes(
        {
            "schema_version": "execution.delivery-manifest-projection@1.0.0",
            "delivery_id": delivery_id,
            "task_id": task_id,
            "manifest_digest": manifest_digest,
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
    attributes = {
        "agentops.delivery.id": delivery_id,
        "agentops.task.id": task_id,
        "agentops.manifest.digest": manifest_digest,
        "agentops.workflow.family": "workflow.implementation",
        "agentops.event.id": f"task-binding-{sha256(delivery_id.encode()).hexdigest()[:24]}",
        "agentops.family.schema": "workflow.implementation@1",
        "agentops.delivery.manifest_projection": projection,
        "agentops.delivery.manifest_projection_digest": sha256(projection.encode()).hexdigest(),
    }
    if display_name is not None:
        attributes["agentops.task.display_name"] = display_name
    return {
        "profile_version": "2.0.0",
        "record_type": "event",
        "event_name": "task.binding",
        "resource": {"service.name": "execution", "service.version": "0.1.3"},
        "scope": {
            "name": "io.agentops.dsh.observation",
            "version": "2.0.0",
            "schema_url": "https://opentelemetry.io/schemas/1.41.0",
        },
        "attributes": attributes,
    }


async def table_count(database_url: str, table: str) -> int:
    async with (
        await psycopg.AsyncConnection.connect(database_url) as connection,
        connection.cursor() as cursor,
    ):
        await cursor.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed test table
        row = await cursor.fetchone()
    assert row is not None
    return int(row[0])


async def clear_core(database_url: str) -> None:
    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        await connection.execute(
            "TRUNCATE delivery_retirement_fences, delivery_terminal_anchors, "
            "delivery_record_memberships, retention_expiry_markers, projection_effects, "
            "accepted_records"
        )


def lifecycle_record(
    original: dict[str, Any], *, event_id: str, review_id: str, fix: bool = False
) -> dict[str, Any]:
    record = deepcopy(original)
    attributes = record["attributes"]
    attributes["agentops.event.id"] = event_id
    attributes["agentops.review.id"] = review_id
    attributes["agentops.source.review.id"] = "review-1"
    attributes["agentops.finding.status"] = "CLOSED_FIXED"
    attributes["agentops.writer.invocation.id"] = f"writer-{review_id}"
    attributes["agentops.reviewer.invocation.id"] = f"reviewer-{review_id}"
    if fix:
        attributes["agentops.fix.id"] = "fix-1"
        attributes["agentops.fix.finding.id"] = "finding-1"
    else:
        attributes["agentops.recheck.id"] = "recheck-1"
        attributes["agentops.recheck.review.id"] = "review-1"
        attributes["agentops.recheck.finding.id"] = "finding-1"
        attributes["agentops.recheck.fix.id"] = "fix-1"
        attributes["agentops.iteration.id"] = "iteration-1"
        attributes["agentops.recheck.role.id"] = "rechecker"
        attributes["agentops.recheck.invocation.id"] = "rechecker-invocation-1"
    return record


def delivery_root(*, trace_id: str = "1" * 32) -> dict[str, Any]:
    return {
        "profile_version": "1.0.0",
        "record_type": "span",
        "span_name": "invoke_workflow delivery-1",
        "trace_id": trace_id,
        "span_id": "1" * 16,
        "span_kind": "INTERNAL",
        "start_time_unix_nano": "100",
        "end_time_unix_nano": "400",
        "span_flags": 1,
        "span_links": [],
        "span_status": "UNSET",
        "resource": {"service.name": "dsh", "service.version": "1"},
        "scope": {
            "name": "io.agentops.dsh.observation",
            "version": "1.0.0",
            "schema_url": "https://opentelemetry.io/schemas/1.41.0",
        },
        "attributes": {
            "agentops.delivery.id": "delivery-1",
            "agentops.workflow.id": "workflow-1",
            "agentops.workflow.version": "1",
            "agentops.implementation.id": "implementation-1",
            "agentops.runtime.id": "runtime-1",
            "agentops.manifest.digest": "b" * 64,
            "agentops.workflow.family": "implementation",
        },
    }


def model_span(
    *, trace_id: str = "1" * 32, span_id: str = "2" * 16, runtime_id: str = "runtime-1"
) -> dict[str, Any]:
    record = delivery_root(trace_id=trace_id)
    record.update(
        {
            "span_name": "chat provider",
            "span_id": span_id,
            "span_kind": "CLIENT",
            "parent_span_id": "1" * 16,
            "start_time_unix_nano": "150",
            "end_time_unix_nano": "250",
        }
    )
    record["attributes"] = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "provider",
        "gen_ai.request.model": "model-alias",
        "agentops.model.id": "canonical-model",
        "agentops.role.id": "implementer",
        "agentops.runtime.id": runtime_id,
    }
    return record


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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_identity_and_projection_commit_as_one_first_write_slice() -> None:
    database_url = os.environ.get("WSR_EVIDENCE_DATABASE_URL")
    if database_url is None:
        pytest.skip("WSR_EVIDENCE_DATABASE_URL is not configured")
    await clear_core(database_url)

    storage = await PostgresStorage.open(database_url)
    service = AdmissionService(storage)
    try:
        results = await asyncio.gather(
            service.admit(finding_record(event_id="event-1")),
            service.admit(finding_record(event_id="event-1")),
        )
        assert {result.disposition for result in results} == {
            Disposition.ACCEPTED,
            Disposition.DUPLICATE,
        }
        assert await table_count(database_url, "accepted_records") == 1

        conflicting_event = finding_record(event_id="event-1")
        conflicting_event["attributes"]["agentops.finding.summary"] = "Changed event content."
        assert (await service.admit(conflicting_event)).disposition is Disposition.CONFLICT

        conflicting_projection = finding_record(event_id="event-2", target_id="artifact-2")
        conflicting_projection["attributes"]["agentops.finding.summary"] = (
            "Changed assertion content."
        )
        assert (await service.admit(conflicting_projection)).disposition is Disposition.CONFLICT
        assert await table_count(database_url, "accepted_records") == 1

        second_target = finding_record(event_id="event-3", target_id="artifact-2")
        assert (await service.admit(second_target)).disposition is Disposition.ACCEPTED
        assert await table_count(database_url, "accepted_records") == 2
    finally:
        await storage.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_task_guard_and_display_conflicts_rollback_the_whole_record() -> None:
    database_url = os.environ.get("WSR_EVIDENCE_DATABASE_URL")
    if database_url is None:
        pytest.skip("WSR_EVIDENCE_DATABASE_URL is not configured")
    await clear_core(database_url)
    storage = await PostgresStorage.open(database_url)
    service = AdmissionService(storage)
    try:
        first = task_binding_record(
            task_id="task-1",
            delivery_id="delivery-1",
            event_id="task-event-1",
            display_name="Token tuning",
        )
        assert (await service.admit(first)).disposition is Disposition.ACCEPTED

        rebound = task_binding_record(
            task_id="task-2",
            delivery_id="delivery-1",
            event_id="task-event-2",
            display_name=None,
        )
        renamed = task_binding_record(
            task_id="task-1",
            delivery_id="delivery-2",
            event_id="task-event-3",
            display_name="Different name",
        )
        assert (await service.admit(rebound)).disposition is Disposition.CONFLICT
        assert (await service.admit(renamed)).disposition is Disposition.CONFLICT
        assert await table_count(database_url, "accepted_records") == 1
        assert await table_count(database_url, "projection_effects") == 5
    finally:
        await storage.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_lifecycle_preconditions_and_restart_retries_leave_no_half_state() -> None:
    database_url = os.environ.get("WSR_EVIDENCE_DATABASE_URL")
    if database_url is None:
        pytest.skip("WSR_EVIDENCE_DATABASE_URL is not configured")
    await clear_core(database_url)
    original = finding_record(event_id="event-original")
    fix = lifecycle_record(original, event_id="event-fix", review_id="review-fix", fix=True)
    recheck = lifecycle_record(original, event_id="event-recheck", review_id="review-recheck")

    storage = await PostgresStorage.open(database_url)
    service = AdmissionService(storage)
    try:
        assert (await service.admit(fix)).disposition is Disposition.REJECTED
        assert await table_count(database_url, "accepted_records") == 0
        assert await table_count(database_url, "projection_effects") == 0
        assert (await service.admit(original)).disposition is Disposition.ACCEPTED
        assert (await service.admit(fix)).disposition is Disposition.ACCEPTED
    finally:
        await storage.close()

    reopened = await PostgresStorage.open(database_url)
    restarted_service = AdmissionService(reopened)
    try:
        assert (await restarted_service.admit(fix)).disposition is Disposition.DUPLICATE
        assert (await restarted_service.admit(recheck)).disposition is Disposition.ACCEPTED
        changed_recheck = deepcopy(recheck)
        changed_recheck["attributes"]["agentops.event.id"] = "event-recheck-conflict"
        changed_recheck["attributes"]["agentops.writer.role.id"] = "different-writer"
        assert (await restarted_service.admit(changed_recheck)).disposition is Disposition.CONFLICT
        changed_assertion = lifecycle_record(
            original, event_id="event-changed", review_id="review-changed", fix=True
        )
        changed_assertion["attributes"]["agentops.fix.id"] = "fix-2"
        changed_assertion["attributes"]["agentops.finding.summary"] = "Changed assertion."
        assert (
            await restarted_service.admit(changed_assertion)
        ).disposition is Disposition.REJECTED
        assert await table_count(database_url, "accepted_records") == 3
    finally:
        await reopened.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_model_attribution_requires_the_matching_delivery_root_atomically() -> None:
    database_url = os.environ.get("WSR_EVIDENCE_DATABASE_URL")
    if database_url is None:
        pytest.skip("WSR_EVIDENCE_DATABASE_URL is not configured")
    await clear_core(database_url)
    storage = await PostgresStorage.open(database_url)
    service = AdmissionService(storage)
    try:
        assert (await service.admit(model_span())).disposition is Disposition.REJECTED
        assert await table_count(database_url, "accepted_records") == 0
        assert (await service.admit(delivery_root())).disposition is Disposition.ACCEPTED
        assert (await service.admit(model_span())).disposition is Disposition.ACCEPTED
        mismatched = model_span(span_id="3" * 16, runtime_id="runtime-2")
        assert (await service.admit(mismatched)).disposition is Disposition.REJECTED
        assert await table_count(database_url, "accepted_records") == 2
    finally:
        await storage.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_versioned_read_model_seam_has_stable_keyset_pagination() -> None:
    database_url = os.environ.get("WSR_EVIDENCE_DATABASE_URL")
    if database_url is None:
        pytest.skip("WSR_EVIDENCE_DATABASE_URL is not configured")
    await clear_core(database_url)
    storage = await PostgresStorage.open(database_url)
    service = AdmissionService(storage)
    try:
        assert CORE_READ_MODEL_VERSION == "1.0.0"
        assert (await service.admit(sampling_record("event-a"))).disposition is (
            Disposition.ACCEPTED
        )
        assert (await service.admit(sampling_record("event-b"))).disposition is (
            Disposition.ACCEPTED
        )

        first_page = await storage.scan_effects(
            kind="factual_contribution", after_key=None, limit=1
        )
        second_page = await storage.scan_effects(
            kind="factual_contribution", after_key=first_page[0].key, limit=1
        )

        assert [page[0].key for page in (first_page, second_page)] == [
            ("sampling.decision", "event-a"),
            ("sampling.decision", "event-b"),
        ]
        assert all(page[0].source_identity[0] == "event" for page in (first_page, second_page))
    finally:
        await storage.close()
