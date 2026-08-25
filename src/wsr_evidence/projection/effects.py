"""Pure projection of validated records into owner-scoped first-write effects."""

from __future__ import annotations

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
    compatibility = (
        str(attributes.get("agentops.family.schema")),
        str(attributes.get("agentops.summary.state")),
        str(attributes.get("agentops.usage.kind")),
        str(attributes.get("agentops.usage.unit")),
        str(attributes.get("agentops.usage.source")),
        str(attributes.get("agentops.usage.source.id")),
    )
    return (
        ProjectionEffect(
            "factual_contribution",
            (record.event_name, attributes["agentops.event.id"]),
            {"attributes": dict(attributes), "compatibility_key": compatibility},
        ),
    )


def _project_span(record: ValidatedRecord) -> tuple[ProjectionEffect, ...]:
    logical = record.logical
    effects = [
        ProjectionEffect(
            "trace_node",
            (logical["trace_id"], logical["span_id"]),
            {
                "span_name": logical["span_name"],
                "span_kind": logical["span_kind"],
                "start_time_unix_nano": logical["start_time_unix_nano"],
                "end_time_unix_nano": logical["end_time_unix_nano"],
                "span_status": logical["span_status"],
                "attributes": dict(record.attributes),
            },
        )
    ]
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
        effects.append(ProjectionEffect("require_finding_target", target, {}))
    else:
        effects.extend(
            (
                ProjectionEffect(
                    "finding_assertion",
                    assertion,
                    {
                        name: a[name]
                        for name in (
                            "agentops.review.lens",
                            "agentops.review.scope",
                            "agentops.review.severity",
                            "agentops.source.review.id",
                            "agentops.family.schema",
                            "agentops.finding.summary",
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
                {"review_id": a["agentops.review.id"]},
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
                    "fix_id": a.get("agentops.recheck.fix.id"),
                    "iteration_id": a["agentops.iteration.id"],
                    "recheck_role_id": a["agentops.recheck.role.id"],
                    "recheck_invocation_id": a["agentops.recheck.invocation.id"],
                },
            )
        )
    return tuple(effects)
