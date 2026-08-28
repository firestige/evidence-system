from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from hashlib import sha256
from typing import Any

import pytest

from wsr_evidence.admission.service import AdmissionService, Disposition
from wsr_evidence.admission.validation import (
    ValidationError,
    canonical_bytes,
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


def task_binding_record(
    *, display_name: str | None = "Token tuning", task_id: str = "task-1"
) -> dict[str, Any]:
    roles: list[dict[str, str]] = []
    projection = {
        "schema_version": "execution.delivery-manifest-projection@1.0.0",
        "delivery_id": "delivery-1",
        "task_id": task_id,
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
    projection_json = canonical_bytes(projection).decode()
    attributes = {
        "agentops.delivery.id": "delivery-1",
        "agentops.task.id": task_id,
        "agentops.manifest.digest": "a" * 64,
        "agentops.event.id": f"task-binding-{sha256(b'delivery-1').hexdigest()[:24]}",
        "agentops.delivery.manifest_projection": projection_json,
        "agentops.delivery.manifest_projection_digest": sha256(
            projection_json.encode()
        ).hexdigest(),
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


def test_profile_two_accepts_task_binding_and_requires_direct_delivery_on_every_record() -> None:
    validated = validate_record(task_binding_record())

    assert validated.profile_version == "2.0.0"
    assert validated.identity == (
        "event",
        f"task-binding-{sha256(b'delivery-1').hexdigest()[:24]}",
    )

    disguised = task_binding_record()
    disguised["profile_version"] = "1.0.0"
    disguised["scope"]["version"] = "1.0.0"
    with pytest.raises(ValidationError, match="unknown EventName"):
        validate_record(disguised)

    profile_two_finding = finding_record()
    profile_two_finding["profile_version"] = "2.0.0"
    profile_two_finding["scope"]["version"] = "2.0.0"
    with pytest.raises(ValidationError, match="direct Delivery"):
        validate_record(profile_two_finding)
    profile_two_finding["attributes"]["agentops.delivery.id"] = "delivery-1"
    assert validate_record(profile_two_finding).profile_version == "2.0.0"

    whitespace = task_binding_record(display_name=" padded ")
    with pytest.raises(ValidationError, match="task display name"):
        validate_record(whitespace)

    for invalid_task_id in ("task id", "?task", "a" * 129):
        with pytest.raises(ValidationError, match=r"task id|agentops\.task\.id"):
            validate_record(task_binding_record(task_id=invalid_task_id))


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
        (
            "delivery_manifest",
            ("a" * 64,),
            {
                "canonical_projection": named[-1].payload["canonical_projection"],
                "projection_digest": named[-1].payload["projection_digest"],
            },
        ),
    ]
    assert [effect.kind for effect in unnamed] == [
        "task_declaration",
        "delivery_task_membership",
        "delivery_task_guard",
        "delivery_manifest",
    ]


def test_task_binding_rejects_noncanonical_mismatched_or_unstable_manifest_projection() -> None:
    base = task_binding_record()
    attributes = base["attributes"]
    projection = json.loads(attributes["agentops.delivery.manifest_projection"])

    cases: list[dict[str, Any]] = []
    for mutate in (
        lambda value: value.update(delivery_id="delivery-other"),
        lambda value: value.update(task_id="task-other"),
        lambda value: value.update(manifest_digest="d" * 64),
        lambda value: value["roles"].append(
            {
                "role_id": "role.writer",
                "role_prompt_identity": "prompt.role.writer",
                "role_prompt_digest": f"sha256:{'e' * 64}",
                "agent_provider_id": "provider.dsh",
                "model_provider_id": "deepseek-official",
                "model_id": "deepseek-reasoner",
                "resolution_source": "REPOSITORY",
            }
        ),
    ):
        candidate = deepcopy(base)
        value = deepcopy(projection)
        mutate(value)
        encoded = canonical_bytes(value).decode()
        candidate["attributes"]["agentops.delivery.manifest_projection"] = encoded
        candidate["attributes"]["agentops.delivery.manifest_projection_digest"] = sha256(
            encoded.encode()
        ).hexdigest()
        cases.append(candidate)

    noncanonical = deepcopy(base)
    noncanonical["attributes"]["agentops.delivery.manifest_projection"] = json.dumps(projection)
    noncanonical["attributes"]["agentops.delivery.manifest_projection_digest"] = sha256(
        json.dumps(projection).encode()
    ).hexdigest()
    cases.append(noncanonical)

    digest_mismatch = deepcopy(base)
    digest_mismatch["attributes"]["agentops.delivery.manifest_projection_digest"] = "f" * 64
    cases.append(digest_mismatch)

    unstable_identity = deepcopy(base)
    unstable_identity["attributes"]["agentops.event.id"] = "task-binding-delivery-1"
    cases.append(unstable_identity)

    for candidate in cases:
        with pytest.raises(ValidationError):
            validate_record(candidate)


def test_task_binding_accepts_present_repository_and_exact_sorted_role_map() -> None:
    candidate = task_binding_record()
    attributes = candidate["attributes"]
    projection = json.loads(attributes["agentops.delivery.manifest_projection"])
    projection["roles"] = [
        {
            "role_id": "role.reviewer",
            "role_prompt_identity": "prompt.role.reviewer",
            "role_prompt_digest": f"sha256:{'d' * 64}",
            "agent_provider_id": "provider.dsh",
            "model_provider_id": "deepseek-official",
            "model_id": "deepseek-chat",
            "resolution_source": "EXECUTION_DEFAULT",
        },
        {
            "role_id": "role.writer",
            "role_prompt_identity": "prompt.role.writer",
            "role_prompt_digest": f"sha256:{'e' * 64}",
            "agent_provider_id": "provider.dsh",
            "model_provider_id": "deepseek-official",
            "model_id": "deepseek-reasoner",
            "resolution_source": "REPOSITORY",
        },
    ]
    resolved = [
        {
            "roleId": role["role_id"],
            "rolePromptIdentity": role["role_prompt_identity"],
            "rolePromptDigest": role["role_prompt_digest"],
            "agentProviderId": role["agent_provider_id"],
            "modelProviderId": role["model_provider_id"],
            "modelId": role["model_id"],
            "resolutionSource": role["resolution_source"],
        }
        for role in projection["roles"]
    ]
    projection["repository_model_bindings"] = {
        "document_state": "PRESENT",
        "document_digest": f"sha256:{'f' * 64}",
        "resolved_map_digest": f"sha256:{canonical_digest(resolved)}",
    }
    encoded = canonical_bytes(projection).decode()
    attributes["agentops.delivery.manifest_projection"] = encoded
    attributes["agentops.delivery.manifest_projection_digest"] = sha256(
        encoded.encode()
    ).hexdigest()

    assert validate_record(candidate).profile_version == "2.0.0"

    reversed_roles = deepcopy(candidate)
    reversed_projection = deepcopy(projection)
    reversed_projection["roles"].reverse()
    reversed_encoded = canonical_bytes(reversed_projection).decode()
    reversed_roles["attributes"]["agentops.delivery.manifest_projection"] = reversed_encoded
    reversed_roles["attributes"]["agentops.delivery.manifest_projection_digest"] = sha256(
        reversed_encoded.encode()
    ).hexdigest()
    with pytest.raises(ValidationError, match="uniquely sorted"):
        validate_record(reversed_roles)


def test_task_binding_rejects_duplicate_secret_and_oversize_projection_bytes() -> None:
    base = task_binding_record()
    encoded = base["attributes"]["agentops.delivery.manifest_projection"]

    duplicate = deepcopy(base)
    duplicate_encoded = encoded.replace(
        '"delivery_id":"delivery-1",',
        '"delivery_id":"delivery-1","delivery_id":"delivery-1",',
        1,
    )
    duplicate["attributes"]["agentops.delivery.manifest_projection"] = duplicate_encoded
    duplicate["attributes"]["agentops.delivery.manifest_projection_digest"] = sha256(
        duplicate_encoded.encode()
    ).hexdigest()
    with pytest.raises(ValidationError, match="duplicate"):
        validate_record(duplicate)

    secret = deepcopy(base)
    secret_projection = json.loads(encoded)
    secret_projection["credential_ref"] = "must-not-land"
    secret_encoded = canonical_bytes(secret_projection).decode()
    secret["attributes"]["agentops.delivery.manifest_projection"] = secret_encoded
    secret["attributes"]["agentops.delivery.manifest_projection_digest"] = sha256(
        secret_encoded.encode()
    ).hexdigest()
    with pytest.raises(ValidationError, match="shape"):
        validate_record(secret)

    oversize = deepcopy(base)
    oversize["attributes"]["agentops.delivery.manifest_projection"] = encoded + " " * 65_536
    with pytest.raises(ValidationError, match="over-limit"):
        validate_record(oversize)


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
