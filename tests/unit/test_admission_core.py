from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any

import pytest

from wsr_evidence.admission.service import AdmissionService, Disposition
from wsr_evidence.admission.validation import ValidationError, validate_record
from wsr_evidence.projection.effects import ProjectionEffect


def finding_record(*, event_id: str = "event-1", target_id: str = "artifact-1") -> dict[str, Any]:
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


class MemoryTransaction:
    def __init__(self, state: dict[str, Any], fail_effects: bool) -> None:
        self.state = state
        self.fail_effects = fail_effects

    async def claim_identity(self, record: Any) -> Disposition:
        existing = self.state["identities"].get(record.identity)
        if existing is None:
            self.state["identities"][record.identity] = record.digest
            return Disposition.ACCEPTED
        return Disposition.DUPLICATE if existing == record.digest else Disposition.CONFLICT

    async def apply_effects(self, effects: tuple[ProjectionEffect, ...]) -> None:
        if self.fail_effects:
            raise RuntimeError("injected projection failure")
        self.state["effects"].extend(effects)


class MemoryStorage:
    def __init__(self, *, fail_effects: bool = False) -> None:
        self.state: dict[str, Any] = {"identities": {}, "effects": []}
        self.fail_effects = fail_effects

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[MemoryTransaction]:
        working = deepcopy(self.state)
        transaction = MemoryTransaction(working, self.fail_effects)
        try:
            yield transaction
        except Exception:
            raise
        else:
            self.state = working


def test_exact_profile_rejects_unknown_and_sibling_family_fields() -> None:
    unknown = finding_record()
    unknown["attributes"]["agentops.unknown"] = "no"
    with pytest.raises(ValidationError, match="unknown agentops field"):
        validate_record(unknown)

    sibling = finding_record()
    sibling["attributes"]["agentops.verification.id"] = "verification-1"
    with pytest.raises(ValidationError, match="prohibited on review.finding"):
        validate_record(sibling)


def test_finding_summary_is_verbatim_bounded_and_not_inferred() -> None:
    record = finding_record()
    validated = validate_record(record)

    assert (
        validated.attributes["agentops.finding.summary"]
        == record["attributes"]["agentops.finding.summary"]
    )

    record["attributes"]["agentops.finding.summary"] = "x" * 513
    with pytest.raises(ValidationError, match="over-limit"):
        validate_record(record)


@pytest.mark.asyncio
async def test_first_write_duplicate_and_conflict_never_overwrite() -> None:
    storage = MemoryStorage()
    service = AdmissionService(storage)

    first = await service.admit(finding_record())
    duplicate = await service.admit(finding_record())
    conflicting = finding_record()
    conflicting["attributes"]["agentops.finding.summary"] = "Changed content is a conflict."
    conflict = await service.admit(conflicting)

    assert first.disposition is Disposition.ACCEPTED
    assert duplicate.disposition is Disposition.DUPLICATE
    assert conflict.disposition is Disposition.CONFLICT
    assert len(storage.state["identities"]) == 1
    assert storage.state["effects"] == list(first.effects)


@pytest.mark.asyncio
async def test_projection_failure_rolls_back_accepted_identity() -> None:
    storage = MemoryStorage(fail_effects=True)
    service = AdmissionService(storage)

    with pytest.raises(RuntimeError, match="injected projection failure"):
        await service.admit(finding_record())

    assert storage.state == {"identities": {}, "effects": []}


def test_finding_projection_is_append_only_and_target_order_independent() -> None:
    first = validate_record(finding_record(event_id="event-a", target_id="artifact-a"))
    second = validate_record(finding_record(event_id="event-b", target_id="artifact-b"))

    first_effects = AdmissionService.project(first)
    second_effects = AdmissionService.project(second)

    assert {effect.key for effect in first_effects if effect.kind == "finding_assertion"} == {
        ("finding-1", "scope-1")
    }
    assert {
        effect.key for effect in first_effects + second_effects if effect.kind == "finding_target"
    } == {
        ("finding-1", "scope-1", "ARTIFACT", "artifact-a", None),
        ("finding-1", "scope-1", "ARTIFACT", "artifact-b", None),
    }
    assert all(effect.operation == "first_write" for effect in first_effects)
