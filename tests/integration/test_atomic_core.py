from __future__ import annotations

import asyncio
import os
from typing import Any

import psycopg
import pytest

from wsr_evidence.admission.service import AdmissionService, Disposition
from wsr_evidence.storage.postgresql import PostgresStorage


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


async def table_count(database_url: str, table: str) -> int:
    async with (
        await psycopg.AsyncConnection.connect(database_url) as connection,
        connection.cursor() as cursor,
    ):
        await cursor.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed test table
        row = await cursor.fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_identity_and_projection_commit_as_one_first_write_slice() -> None:
    database_url = os.environ.get("WSR_EVIDENCE_DATABASE_URL")
    if database_url is None:
        pytest.skip("WSR_EVIDENCE_DATABASE_URL is not configured")
    async with (
        await psycopg.AsyncConnection.connect(database_url) as connection,
        connection.cursor() as cursor,
    ):
        await cursor.execute("TRUNCATE projection_effects, accepted_records")

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
