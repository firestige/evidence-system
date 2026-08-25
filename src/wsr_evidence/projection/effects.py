"""Pure projection of validated records into owner-scoped first-write effects."""

from __future__ import annotations

from typing import Any

from wsr_evidence.model import ProjectionEffect, ValidatedRecord


def project(record: ValidatedRecord) -> tuple[ProjectionEffect, ...]:
    if record.record_type == "span":
        return _project_span(record)
    if record.event_name == "review.finding":
        return _project_finding(record)
    if record.event_name == "role.lineage":
        attributes = record.attributes
        return (
            ProjectionEffect(
                "role_lineage",
                (
                    attributes["agentops.family.schema"],
                    attributes["agentops.role.id"],
                ),
                dict(attributes),
            ),
        )
    attributes = record.attributes
    compatibility = _compatibility_coordinates(record)
    completeness = attributes.get("agentops.summary.state")
    return (
        ProjectionEffect(
            "factual_contribution",
            (record.event_name, attributes["agentops.event.id"]),
            {
                "attributes": dict(attributes),
                "compatibility_key": compatibility,
                "aggregate_eligible": completeness in {"FINAL", "LOWER_BOUND"}
                and all(value is not None for value in compatibility),
            },
        ),
    )


def _compatibility_coordinates(record: ValidatedRecord) -> tuple[Any, ...]:
    a = record.attributes
    base = (a.get("agentops.family.schema"), record.event_name, a.get("agentops.summary.state"))
    if record.event_name == "usage":
        return (
            *base,
            a.get("agentops.usage.kind"),
            a.get("agentops.usage.unit"),
            a.get("agentops.usage.source"),
            a.get("agentops.usage.source.id"),
        )
    if record.event_name == "implementation.summary":
        return (
            *base,
            a.get("agentops.coverage.dimension"),
            a.get("agentops.coverage.scope"),
            a.get("agentops.coverage.tool.id"),
            a.get("agentops.coverage.format"),
        )
    if record.event_name == "test.summary":
        return (*base, a.get("agentops.artifact.id"), a.get("agentops.artifact.digest"))
    if record.event_name == "review.summary":
        return (*base, a.get("agentops.review.lens"), a.get("agentops.review.scope"))
    return base


def _project_span(record: ValidatedRecord) -> tuple[ProjectionEffect, ...]:
    logical = record.logical
    effects: list[ProjectionEffect] = []
    if logical["span_name"].startswith("invoke_workflow"):
        family_schema = {
            "implementation": "implementation@1",
            "system-design": "system-design@1",
        }[str(record.attributes["agentops.workflow.family"])]
        effects.append(
            ProjectionEffect(
                "delivery_root_binding",
                (logical["trace_id"],),
                {
                    "delivery_id": record.attributes["agentops.delivery.id"],
                    "runtime_id": record.attributes["agentops.runtime.id"],
                    "manifest_digest": record.attributes["agentops.manifest.digest"],
                    "family_schema": family_schema,
                },
            )
        )
    if "agentops.model.id" in record.attributes:
        effects.append(
            ProjectionEffect(
                "require_delivery_root_binding",
                (logical["trace_id"],),
                {"runtime_id": record.attributes["agentops.runtime.id"]},
            )
        )
        effects.append(
            ProjectionEffect(
                "model_attribution",
                (
                    record.attributes["gen_ai.provider.name"],
                    record.attributes["agentops.model.id"],
                    record.attributes["agentops.role.id"],
                    record.attributes["agentops.runtime.id"],
                    logical["trace_id"],
                    logical["span_id"],
                ),
                {"request_model": record.attributes["gen_ai.request.model"]},
            )
        )
    effects.append(
        ProjectionEffect(
            "trace_node",
            (logical["trace_id"], logical["span_id"]),
            {
                "span_name": logical["span_name"],
                "span_kind": logical["span_kind"],
                "start_time_unix_nano": logical["start_time_unix_nano"],
                "end_time_unix_nano": logical["end_time_unix_nano"],
                "span_status": logical["span_status"],
                "span_flags": logical["span_flags"],
                "trace_state": logical.get("trace_state"),
                "attributes": dict(record.attributes),
            },
        )
    )
    parent = logical.get("parent_span_id")
    if parent:
        effects.append(
            ProjectionEffect(
                "trace_parent_edge",
                (logical["trace_id"], logical["span_id"], parent),
                {},
            )
        )
    for link in logical["span_links"]:
        effects.append(
            ProjectionEffect(
                "trace_link",
                (logical["trace_id"], logical["span_id"], link["trace_id"], link["span_id"]),
                dict(link),
            )
        )
    return tuple(effects)


def _project_finding(record: ValidatedRecord) -> tuple[ProjectionEffect, ...]:
    a = record.attributes
    assertion = (a["agentops.finding.id"], a["agentops.finding.scope.id"])
    target = (
        *assertion,
        a["agentops.finding.target.kind"],
        a["agentops.finding.target.id"],
        a.get("agentops.finding.target.artifact.id"),
    )
    lifecycle = "agentops.fix.id" in a or "agentops.recheck.id" in a
    invariant_names = (
        "agentops.review.lens",
        "agentops.review.scope",
        "agentops.review.severity",
        "agentops.source.review.id",
        "agentops.family.schema",
        "agentops.finding.summary",
    )
    effects = [
        ProjectionEffect(
            "finding_scope_guard",
            (a["agentops.finding.id"],),
            {"scope_id": a["agentops.finding.scope.id"]},
        ),
        ProjectionEffect(
            "finding_target_guard",
            (*assertion, a["agentops.finding.target.id"]),
            {
                "kind": a["agentops.finding.target.kind"],
                "containing_artifact_id": a.get("agentops.finding.target.artifact.id"),
            },
        ),
    ]
    if lifecycle:
        effects.extend(
            (
                ProjectionEffect(
                    "require_finding_assertion",
                    assertion,
                    {name: a[name] for name in invariant_names},
                ),
                ProjectionEffect("require_finding_target", target, {}),
            )
        )
    else:
        effects.extend(
            (
                ProjectionEffect(
                    "finding_assertion",
                    assertion,
                    {
                        name: a[name]
                        for name in (
                            *invariant_names,
                            "agentops.artifact.id",
                            "agentops.artifact.digest",
                            "agentops.writer.role.id",
                            "agentops.writer.invocation.id",
                            "agentops.reviewer.role.id",
                            "agentops.reviewer.invocation.id",
                        )
                    },
                ),
                ProjectionEffect("finding_target", target, {}),
            )
        )
    effects.append(
        ProjectionEffect(
            "finding_status",
            (*assertion, a["agentops.review.id"]),
            {
                "status": a["agentops.finding.status"],
                "writer_role_id": a["agentops.writer.role.id"],
                "writer_invocation_id": a["agentops.writer.invocation.id"],
                "reviewer_role_id": a["agentops.reviewer.role.id"],
                "reviewer_invocation_id": a["agentops.reviewer.invocation.id"],
            },
        )
    )
    if "agentops.fix.id" in a:
        effects.append(
            ProjectionEffect(
                "finding_fix",
                (*target, a["agentops.fix.id"]),
                {
                    "finding_id": a["agentops.fix.finding.id"],
                    "review_id": a["agentops.review.id"],
                    "writer_role_id": a["agentops.writer.role.id"],
                    "writer_invocation_id": a["agentops.writer.invocation.id"],
                    "reviewer_role_id": a["agentops.reviewer.role.id"],
                    "reviewer_invocation_id": a["agentops.reviewer.invocation.id"],
                },
            )
        )
    if "agentops.recheck.id" in a:
        if "agentops.recheck.fix.id" in a:
            effects.append(
                ProjectionEffect(
                    "require_finding_fix",
                    (*target, a["agentops.recheck.fix.id"]),
                    {},
                )
            )
        effects.append(
            ProjectionEffect(
                "finding_recheck",
                (*target, a["agentops.recheck.id"]),
                {
                    "review_id": a["agentops.review.id"],
                    "prior_review_id": a["agentops.recheck.review.id"],
                    "finding_id": a["agentops.recheck.finding.id"],
                    "fix_id": a.get("agentops.recheck.fix.id"),
                    "iteration_id": a["agentops.iteration.id"],
                    "writer_role_id": a["agentops.writer.role.id"],
                    "writer_invocation_id": a["agentops.writer.invocation.id"],
                    "reviewer_role_id": a["agentops.reviewer.role.id"],
                    "reviewer_invocation_id": a["agentops.reviewer.invocation.id"],
                    "recheck_role_id": a["agentops.recheck.role.id"],
                    "recheck_invocation_id": a["agentops.recheck.invocation.id"],
                },
            )
        )
    return tuple(effects)
