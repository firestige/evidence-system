from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any

import pytest

from wsr_evidence.admission.service import AdmissionService, Disposition
from wsr_evidence.admission.validation import (
    ValidationError,
    canonical_digest,
    validate_record,
)
from wsr_evidence.projection.effects import ProjectionEffect
from wsr_evidence.storage.postgresql import _accepted_record_values


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


def task_binding_record(*, display_name: str | None = "Token tuning") -> dict[str, Any]:
    attributes = {
        "agentops.delivery.id": "delivery-1",
        "agentops.task.id": "task-1",
        "agentops.manifest.digest": "a" * 64,
        "agentops.event.id": "task-binding-delivery-1",
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


def test_profile_two_accepts_only_the_closed_task_binding_carrier() -> None:
    validated = validate_record(task_binding_record())

    assert validated.profile_version == "2.0.0"
    assert validated.identity == ("event", "task-binding-delivery-1")

    disguised = task_binding_record()
    disguised["profile_version"] = "1.0.0"
    disguised["scope"]["version"] = "1.0.0"
    with pytest.raises(ValidationError, match="unknown EventName"):
        validate_record(disguised)

    whitespace = task_binding_record(display_name=" padded ")
    with pytest.raises(ValidationError, match="task display name"):
        validate_record(whitespace)


def test_task_binding_projects_atomic_identity_membership_guard_and_optional_name() -> None:
    named = AdmissionService.project(validate_record(task_binding_record()))
    unnamed = AdmissionService.project(validate_record(task_binding_record(display_name=None)))

    assert [(effect.kind, effect.key, effect.payload) for effect in named] == [
        ("task_declaration", ("task-1",), {}),
        (
            "delivery_task_membership",
            ("task-1", "delivery-1"),
            {"manifest_digest": "a" * 64},
        ),
        (
            "delivery_task_guard",
            ("delivery-1",),
            {"task_id": "task-1", "manifest_digest": "a" * 64},
        ),
        ("task_display_name", ("task-1",), {"display_name": "Token tuning"}),
    ]
    assert [effect.kind for effect in unnamed] == [
        "task_declaration",
        "delivery_task_membership",
        "delivery_task_guard",
    ]


def test_task_binding_persists_its_exact_profile_coordinate() -> None:
    record = validate_record(task_binding_record())

    values = _accepted_record_values(record)

    assert values[3] == "2.0.0"


def test_family_specific_summary_and_fresh_reader_shapes_are_closed() -> None:
    implementation = {
        "profile_version": "1.0.0",
        "record_type": "event",
        "event_name": "implementation.summary",
        "resource": {"service.name": "dsh", "service.version": "1"},
        "scope": {
            "name": "io.agentops.dsh.observation",
            "version": "1.0.0",
            "schema_url": "https://opentelemetry.io/schemas/1.41.0",
        },
        "attributes": {
            "agentops.event.id": "implementation-1",
            "agentops.workflow.family": "system-design",
            "agentops.family.schema": "system-design@1",
            "agentops.summary.state": "FINAL",
            "agentops.artifact.id": "report-1",
            "agentops.artifact.digest": "a" * 64,
            "agentops.coverage.dimension": "line",
            "agentops.coverage.covered": 10,
            "agentops.coverage.total": 10,
            "agentops.coverage.scope": "src",
            "agentops.coverage.tool.id": "coverage-tool",
            "agentops.coverage.format": "coverage-json",
        },
    }
    with pytest.raises(ValidationError, match="family-specific EventName"):
        validate_record(implementation)

    fresh_reader = finding_record()
    fresh_reader["event_name"] = "review.summary"
    attributes = fresh_reader["attributes"]
    for name in (
        "agentops.review.severity",
        "agentops.finding.id",
        "agentops.finding.status",
        "agentops.source.review.id",
        "agentops.finding.summary",
        "agentops.finding.scope.id",
        "agentops.finding.target.kind",
        "agentops.finding.target.id",
    ):
        attributes.pop(name)
    attributes["agentops.summary.state"] = "FINAL"
    attributes["agentops.review.lens"] = "FRESH_READER"
    attributes["agentops.workflow.family"] = "system-design"
    attributes["agentops.family.schema"] = "system-design@1"
    with pytest.raises(ValidationError, match="Fresh Reader"):
        validate_record(fresh_reader)


def test_rfc8785_reference_digest_vector_is_stable() -> None:
    assert canonical_digest({"b": 2, "a": 1}) == (
        "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777"
    )


def test_canonical_digest_matches_frozen_javascript_number_serialization() -> None:
    assert canonical_digest({"z": 1e-7, "a": 1.0, "b": -0.0, "c": 1e20}) == (
        "f4e15fe240a9fa9acc1677d5faaccf8b3378b9754634035967232cb19cc6992f"
    )


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


def test_lifecycle_projection_rechecks_assertion_and_preserves_exact_provenance() -> None:
    lifecycle = finding_record(event_id="event-fix")
    attributes = lifecycle["attributes"]
    attributes["agentops.review.id"] = "review-fix"
    attributes["agentops.source.review.id"] = "review-1"
    attributes["agentops.finding.status"] = "CLOSED_FIXED"
    attributes["agentops.writer.invocation.id"] = "writer-invocation-fix"
    attributes["agentops.reviewer.invocation.id"] = "reviewer-invocation-fix"
    attributes["agentops.fix.id"] = "fix-1"
    attributes["agentops.fix.finding.id"] = "finding-1"

    effects = AdmissionService.project(validate_record(lifecycle))
    required_assertion = next(
        effect for effect in effects if effect.kind == "require_finding_assertion"
    )
    fix = next(effect for effect in effects if effect.kind == "finding_fix")

    assert required_assertion.payload["agentops.finding.summary"] == (
        "The accepted assertion remains factual."
    )
    assert fix.payload == {
        "finding_id": "finding-1",
        "review_id": "review-fix",
        "writer_role_id": "writer",
        "writer_invocation_id": "writer-invocation-fix",
        "reviewer_role_id": "reviewer",
        "reviewer_invocation_id": "reviewer-invocation-fix",
    }


def test_usage_compatibility_keeps_units_and_missingness_separate() -> None:
    def usage(event_id: str, unit: str, state: str) -> dict[str, Any]:
        return {
            "profile_version": "1.0.0",
            "record_type": "event",
            "event_name": "usage",
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
                "agentops.summary.state": state,
                "agentops.usage.kind": "money",
                "agentops.usage.unit": unit,
                "agentops.usage.source": "provider",
                "agentops.usage.source.id": "provider-1",
                "agentops.usage.value": 0,
            },
        }

    usd = AdmissionService.project(validate_record(usage("usage-1", "USD", "FINAL")))[0]
    eur = AdmissionService.project(validate_record(usage("usage-2", "EUR", "FINAL")))[0]
    unavailable = AdmissionService.project(validate_record(usage("usage-3", "USD", "UNAVAILABLE")))[
        0
    ]

    assert usd.payload["compatibility_key"] != eur.payload["compatibility_key"]
    assert usd.payload["aggregate_eligible"] is True
    assert unavailable.payload["aggregate_eligible"] is False
