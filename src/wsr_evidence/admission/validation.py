"""Closed Observation Profile 1.0.0 validation and stable identity."""

from __future__ import annotations

import json
import math
import re
from decimal import Decimal
from hashlib import sha256
from typing import Any, cast

from wsr_evidence.model import ValidatedRecord

PROFILE_VERSION = "1.0.0"
SCOPE = {
    "name": "io.agentops.dsh.observation",
    "version": "1.0.0",
    "schema_url": "https://opentelemetry.io/schemas/1.41.0",
}
RESOURCE_FIELDS = {"service.name", "service.version"}
EVENT_NAMES = {
    "delivery.summary",
    "review.finding",
    "review.summary",
    "test.summary",
    "implementation.summary",
    "system_design.summary",
    "role.lineage",
    "intervention",
    "usage",
    "sampling.decision",
}
STANDARD_TYPES = {
    **{
        name: "string"
        for name in (
            "gen_ai.operation.name",
            "gen_ai.agent.id",
            "gen_ai.agent.name",
            "gen_ai.agent.version",
            "gen_ai.provider.name",
            "gen_ai.request.model",
            "gen_ai.response.model",
            "gen_ai.tool.name",
            "gen_ai.tool.type",
            "gen_ai.tool.call.id",
            "error.type",
        )
    },
    "gen_ai.usage.input_tokens": "integer",
    "gen_ai.usage.output_tokens": "integer",
}
INTEGER_FIELDS = {
    "agentops.review.total",
    "agentops.review.observed.count",
    "agentops.observed.loop.count",
    "agentops.observed.intervention.count",
    "agentops.usage.value",
    "agentops.test.passed",
    "agentops.test.failed",
    "agentops.test.skipped",
    "agentops.coverage.covered",
    "agentops.coverage.total",
    "agentops.fresh_reader.finding.count",
    "agentops.verification.check.passed",
    "agentops.verification.check.failed",
}
NUMBER_FIELDS = {
    "agentops.sampling.probability",
    "agentops.delivery.elapsed_time_ms",
    "agentops.test.duration.seconds",
}
COMMON_STRING_FIELDS = {
    "agentops.delivery.id",
    "agentops.task.id",
    "agentops.workflow.id",
    "agentops.workflow.version",
    "agentops.implementation.id",
    "agentops.runtime.id",
    "agentops.manifest.digest",
    "agentops.workflow.family",
    "agentops.event.id",
    "agentops.delivery.outcome",
    "agentops.summary.state",
    "agentops.review.id",
    "agentops.review.lens",
    "agentops.review.scope",
    "agentops.review.severity",
    "agentops.finding.id",
    "agentops.finding.status",
    "agentops.source.review.id",
    "agentops.fix.id",
    "agentops.fix.finding.id",
    "agentops.recheck.id",
    "agentops.recheck.review.id",
    "agentops.recheck.finding.id",
    "agentops.recheck.fix.id",
    "agentops.iteration.id",
    "agentops.artifact.id",
    "agentops.artifact.digest",
    "agentops.role.id",
    "agentops.role.lineage.id",
    "agentops.parent.role.id",
    "agentops.writer.role.id",
    "agentops.reviewer.role.id",
    "agentops.recheck.role.id",
    "agentops.writer.invocation.id",
    "agentops.reviewer.invocation.id",
    "agentops.recheck.invocation.id",
    "agentops.intervention.kind",
    "agentops.usage.kind",
    "agentops.usage.unit",
    "agentops.usage.source",
    "agentops.usage.source.id",
    "agentops.sampling.decision",
    "agentops.family.schema",
    "agentops.finding.summary",
    "agentops.finding.scope.id",
    "agentops.finding.target.kind",
    "agentops.finding.target.id",
    "agentops.finding.target.artifact.id",
    "agentops.delivery.stage.reached",
    "agentops.model.id",
}
FAMILY_STRING_FIELDS = {
    "agentops.coverage.dimension",
    "agentops.coverage.scope",
    "agentops.coverage.tool.id",
    "agentops.coverage.format",
    "agentops.fresh_reader.result",
    "agentops.verification.id",
    "agentops.verification.result",
}
FIELD_TYPES = {
    **{name: "string" for name in COMMON_STRING_FIELDS | FAMILY_STRING_FIELDS},
    **{name: "integer" for name in INTEGER_FIELDS},
    **{name: "number" for name in NUMBER_FIELDS},
}
ENUMS = {
    "agentops.workflow.family": {"implementation", "system-design"},
    "agentops.delivery.outcome": {"COMPLETED", "INCOMPLETE", "FAILED", "CANCELLED", "START_FAILED"},
    "agentops.summary.state": {"FINAL", "LOWER_BOUND", "NOT_APPLICABLE", "UNAVAILABLE"},
    "agentops.review.lens": {
        "GOAL_BLACKBOX",
        "IMPLEMENTATION_WHITEBOX",
        "ARCHITECTURE",
        "PROBLEM_SOLUTION",
        "QUALITY_ACCEPTANCE",
        "FRESH_READER",
    },
    "agentops.review.severity": {"BLOCKING", "MAJOR", "MINOR"},
    "agentops.finding.status": {"OPEN", "CLOSED_FIXED", "CLOSED_NOT_VALID", "ACCEPTED_MINOR"},
    "agentops.intervention.kind": {"USER_REDIRECT"},
    "agentops.usage.kind": {
        "native_credit",
        "request",
        "premium_request",
        "provider_native",
        "money",
    },
    "agentops.usage.source": {"runtime", "provider"},
    "agentops.sampling.decision": {"RECORD_AND_SAMPLE", "DROP"},
    "agentops.family.schema": {"implementation@1", "system-design@1"},
    "agentops.finding.target.kind": {"ARTIFACT", "SECTION", "COMPONENT", "REQUIREMENT"},
    "agentops.coverage.dimension": {"line", "branch", "function"},
    "agentops.fresh_reader.result": {"PASS", "FINDINGS_REPORTED"},
    "agentops.verification.result": {"PASS", "FAIL", "INCONCLUSIVE", "KNOWN_RED_NO_DELTA"},
}


def _set(value: str) -> set[str]:
    return set(value.split())


EVENT_RULES = {
    "delivery.summary": (
        _set(
            "agentops.workflow.family agentops.event.id agentops.delivery.outcome agentops.summary.state agentops.role.id agentops.family.schema agentops.delivery.elapsed_time_ms agentops.delivery.stage.reached"
        ),
        _set(
            "agentops.workflow.family agentops.event.id agentops.delivery.outcome agentops.summary.state agentops.family.schema"
        ),
    ),
    "review.finding": (
        _set(
            "agentops.workflow.family agentops.event.id agentops.review.id agentops.review.lens agentops.review.scope agentops.review.severity agentops.finding.id agentops.finding.status agentops.source.review.id agentops.fix.id agentops.fix.finding.id agentops.recheck.id agentops.recheck.review.id agentops.recheck.finding.id agentops.recheck.fix.id agentops.iteration.id agentops.artifact.id agentops.artifact.digest agentops.writer.role.id agentops.reviewer.role.id agentops.recheck.role.id agentops.writer.invocation.id agentops.reviewer.invocation.id agentops.recheck.invocation.id agentops.family.schema agentops.finding.summary agentops.finding.scope.id agentops.finding.target.kind agentops.finding.target.id agentops.finding.target.artifact.id"
        ),
        _set(
            "agentops.workflow.family agentops.event.id agentops.review.id agentops.review.lens agentops.review.scope agentops.review.severity agentops.finding.id agentops.finding.status agentops.source.review.id agentops.artifact.id agentops.artifact.digest agentops.writer.role.id agentops.reviewer.role.id agentops.writer.invocation.id agentops.reviewer.invocation.id agentops.family.schema agentops.finding.summary agentops.finding.scope.id agentops.finding.target.kind agentops.finding.target.id"
        ),
    ),
    "review.summary": (
        _set(
            "agentops.workflow.family agentops.event.id agentops.summary.state agentops.review.id agentops.review.lens agentops.review.scope agentops.review.total agentops.review.observed.count agentops.recheck.id agentops.recheck.review.id agentops.recheck.finding.id agentops.recheck.fix.id agentops.iteration.id agentops.artifact.id agentops.artifact.digest agentops.writer.role.id agentops.reviewer.role.id agentops.recheck.role.id agentops.writer.invocation.id agentops.reviewer.invocation.id agentops.recheck.invocation.id agentops.family.schema agentops.fresh_reader.result agentops.fresh_reader.finding.count"
        ),
        _set(
            "agentops.workflow.family agentops.event.id agentops.summary.state agentops.review.id agentops.review.lens agentops.review.scope agentops.artifact.id agentops.artifact.digest agentops.writer.role.id agentops.reviewer.role.id agentops.writer.invocation.id agentops.reviewer.invocation.id agentops.family.schema"
        ),
    ),
    "test.summary": (
        _set(
            "agentops.workflow.family agentops.event.id agentops.summary.state agentops.artifact.id agentops.artifact.digest agentops.role.id agentops.family.schema agentops.test.passed agentops.test.failed agentops.test.skipped agentops.test.duration.seconds"
        ),
        _set(
            "agentops.workflow.family agentops.event.id agentops.summary.state agentops.artifact.id agentops.artifact.digest agentops.family.schema agentops.test.passed agentops.test.failed agentops.test.skipped"
        ),
    ),
    "implementation.summary": (
        _set(
            "agentops.workflow.family agentops.event.id agentops.summary.state agentops.artifact.id agentops.artifact.digest agentops.role.id agentops.observed.loop.count agentops.observed.intervention.count agentops.family.schema agentops.coverage.dimension agentops.coverage.covered agentops.coverage.total agentops.coverage.scope agentops.coverage.tool.id agentops.coverage.format"
        ),
        _set(
            "agentops.workflow.family agentops.event.id agentops.summary.state agentops.artifact.id agentops.artifact.digest agentops.family.schema agentops.coverage.dimension agentops.coverage.covered agentops.coverage.total agentops.coverage.scope agentops.coverage.tool.id agentops.coverage.format"
        ),
    ),
    "system_design.summary": (
        _set(
            "agentops.workflow.family agentops.event.id agentops.summary.state agentops.artifact.id agentops.artifact.digest agentops.role.id agentops.observed.loop.count agentops.observed.intervention.count agentops.family.schema agentops.verification.id agentops.verification.result agentops.verification.check.passed agentops.verification.check.failed"
        ),
        _set(
            "agentops.workflow.family agentops.event.id agentops.summary.state agentops.artifact.id agentops.artifact.digest agentops.family.schema agentops.verification.id agentops.verification.result agentops.verification.check.passed agentops.verification.check.failed"
        ),
    ),
    "role.lineage": (
        _set(
            "agentops.workflow.family agentops.event.id agentops.role.id agentops.role.lineage.id agentops.parent.role.id agentops.family.schema"
        ),
        _set(
            "agentops.workflow.family agentops.event.id agentops.role.id agentops.role.lineage.id agentops.family.schema"
        ),
    ),
    "intervention": (
        _set(
            "agentops.workflow.family agentops.event.id agentops.role.id agentops.intervention.kind agentops.family.schema"
        ),
        _set(
            "agentops.workflow.family agentops.event.id agentops.intervention.kind agentops.family.schema"
        ),
    ),
    "usage": (
        _set(
            "agentops.workflow.family agentops.event.id agentops.summary.state agentops.role.id agentops.usage.kind agentops.usage.unit agentops.usage.source agentops.usage.source.id agentops.usage.value agentops.family.schema"
        ),
        _set(
            "agentops.workflow.family agentops.event.id agentops.summary.state agentops.usage.kind agentops.usage.unit agentops.usage.source agentops.usage.source.id agentops.usage.value agentops.family.schema"
        ),
    ),
    "sampling.decision": (
        _set("agentops.event.id agentops.sampling.decision agentops.sampling.probability"),
        _set("agentops.event.id agentops.sampling.decision agentops.sampling.probability"),
    ),
}
SPAN_ALLOWED = _set(
    "agentops.delivery.id agentops.task.id agentops.workflow.id agentops.workflow.version agentops.implementation.id agentops.runtime.id agentops.manifest.digest agentops.workflow.family agentops.role.id agentops.model.id"
)
RECORD_KEYS = _set(
    "profile_version record_type event_name span_name trace_id span_id span_kind start_time_unix_nano end_time_unix_nano parent_span_id trace_state span_flags span_links span_status resource scope attributes"
)
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")
TRACE_ID = re.compile(r"^[a-f0-9]{32}$")
SPAN_ID = re.compile(r"^[a-f0-9]{16}$")
NANOSECONDS = re.compile(r"^(0|[1-9][0-9]{0,19})$")


class ValidationError(ValueError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return _canonical_text(value).encode()


def _canonical_number(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("canonical JSON prohibits non-finite numbers")
    if value == 0:
        return "0"
    encoded = repr(value).lower()
    magnitude = abs(value)
    if 1e-6 <= magnitude < 1e21:
        if "e" in encoded:
            return format(Decimal(encoded), "f")
        return encoded.removesuffix(".0")
    if "e" not in encoded:
        return encoded.removesuffix(".0")
    mantissa, exponent = encoded.split("e")
    mantissa = mantissa.removesuffix(".0")
    exponent_value = int(exponent)
    exponent_text = f"+{exponent_value}" if exponent_value >= 0 else str(exponent_value)
    return f"{mantissa}e{exponent_text}"


def _canonical_text(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _canonical_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list | tuple):
        return "[" + ",".join(_canonical_text(item) for item in value) + "]"
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                f"{_canonical_text(key)}:{_canonical_text(value[key])}" for key in sorted(value)
            )
            + "}"
        )
    raise TypeError(f"unsupported canonical JSON type: {type(value).__name__}")


def canonical_digest(value: Any) -> str:
    return sha256(canonical_bytes(value)).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _validate_envelope(record: dict[str, Any]) -> None:
    _require(set(record) <= RECORD_KEYS, "unknown record field")
    _require(record.get("profile_version") == PROFILE_VERSION, "unsupported profile version")
    resource = record.get("resource")
    _require(
        isinstance(resource, dict) and set(resource) == RESOURCE_FIELDS, "invalid Resource shape"
    )
    for value in cast(dict[str, Any], resource).values():
        _require(isinstance(value, str) and 1 <= len(value) <= 128, "invalid Resource value")
    _require(record.get("scope") == SCOPE, "invalid Scope/profile pin")
    _require(isinstance(record.get("attributes"), dict), "attributes must be an object")


def _validate_attributes(attributes: dict[str, Any]) -> None:
    for name, value in attributes.items():
        field_type = FIELD_TYPES.get(name) or STANDARD_TYPES.get(name)
        if name.startswith("agentops.") and field_type is None:
            raise ValidationError(f"unknown agentops field {name}")
        if not name.startswith("agentops.") and field_type is None:
            raise ValidationError(f"unknown standard field {name}")
        if field_type == "string":
            maximum = 512 if name == "agentops.finding.summary" else 128
            _require(
                isinstance(value, str) and not isinstance(value, bool), f"wrong type for {name}"
            )
            _require(len(value) > 0, f"empty {name}")
            _require(len(value) <= maximum, f"over-limit {name}")
        elif field_type == "integer":
            _require(
                isinstance(value, int) and not isinstance(value, bool), f"wrong type for {name}"
            )
            _require(value >= 0, f"negative {name}")
        else:
            _require(
                isinstance(value, int | float) and not isinstance(value, bool),
                f"wrong type for {name}",
            )
            _require(math.isfinite(value), f"non-finite {name}")
        if name in ENUMS:
            _require(value in ENUMS[name], f"invalid enum for {name}")
        if name in {"agentops.manifest.digest", "agentops.artifact.digest"}:
            _require(
                isinstance(value, str) and DIGEST.fullmatch(value) is not None,
                f"invalid digest {name}",
            )


def _validate_span(record: dict[str, Any], attributes: dict[str, Any]) -> None:
    required = _set(
        "span_name trace_id span_id span_kind start_time_unix_nano end_time_unix_nano span_flags span_links span_status"
    )
    _require(required <= set(record), "incomplete Span shape")
    _require("event_name" not in record, "event_name prohibited on Span")
    _require(
        isinstance(record["span_name"], str) and 1 <= len(record["span_name"]) <= 128,
        "invalid span_name",
    )
    _require(
        isinstance(record["trace_id"], str) and TRACE_ID.fullmatch(record["trace_id"]) is not None,
        "invalid trace_id",
    )
    _require(
        isinstance(record["span_id"], str) and SPAN_ID.fullmatch(record["span_id"]) is not None,
        "invalid span_id",
    )
    if "parent_span_id" in record:
        _require(
            isinstance(record["parent_span_id"], str)
            and SPAN_ID.fullmatch(record["parent_span_id"]) is not None,
            "invalid parent_span_id",
        )
    _require(record["span_kind"] in {"INTERNAL", "CLIENT"}, "invalid Span kind")
    _require(record["span_status"] in {"UNSET", "OK", "ERROR"}, "invalid Span status")
    for field in ("start_time_unix_nano", "end_time_unix_nano"):
        _require(
            isinstance(record[field], str) and NANOSECONDS.fullmatch(record[field]) is not None,
            f"invalid {field}",
        )
    _require(
        int(record["end_time_unix_nano"]) >= int(record["start_time_unix_nano"]),
        "Span end precedes start",
    )
    _require(
        isinstance(record["span_flags"], int)
        and not isinstance(record["span_flags"], bool)
        and 0 <= record["span_flags"] <= 0xFFFFFFFF,
        "invalid Span flags",
    )
    if "trace_state" in record:
        _require(
            isinstance(record["trace_state"], str) and len(record["trace_state"]) <= 512,
            "invalid trace_state",
        )
    _require(
        isinstance(record["span_links"], list) and len(record["span_links"]) <= 128,
        "invalid Span links",
    )
    for link in record["span_links"]:
        _require(
            isinstance(link, dict)
            and {"trace_id", "span_id"} <= set(link)
            and set(link) <= {"trace_id", "span_id", "trace_state", "flags"},
            "invalid Span link shape",
        )
        _require(
            isinstance(link["trace_id"], str)
            and TRACE_ID.fullmatch(link["trace_id"]) is not None
            and isinstance(link["span_id"], str)
            and SPAN_ID.fullmatch(link["span_id"]) is not None,
            "invalid Span link identity",
        )
        if "trace_state" in link:
            _require(
                isinstance(link["trace_state"], str) and len(link["trace_state"]) <= 512,
                "invalid Span link trace_state",
            )
        if "flags" in link:
            _require(
                isinstance(link["flags"], int)
                and not isinstance(link["flags"], bool)
                and 0 <= link["flags"] <= 0xFFFFFFFF,
                "invalid Span link flags",
            )
    disallowed = {name for name in attributes if name.startswith("agentops.")} - SPAN_ALLOWED
    if disallowed:
        raise ValidationError(f"{sorted(disallowed)[0]} prohibited on Span")
    delivery_root = record["span_name"].startswith("invoke_workflow")
    root_fields = _set(
        "agentops.delivery.id agentops.workflow.id agentops.workflow.version agentops.implementation.id agentops.runtime.id agentops.manifest.digest agentops.workflow.family"
    )
    root_only = root_fields - {"agentops.runtime.id"}
    if delivery_root:
        _require(root_fields <= set(attributes), "incomplete Delivery root")
        _require(record["span_kind"] == "INTERNAL", "Delivery root must use INTERNAL Span kind")
    else:
        _require(
            not (root_only & set(attributes)), "Delivery-root field outside Delivery root Span"
        )
    operation = attributes.get("gen_ai.operation.name")
    if operation == "invoke_agent":
        _require("gen_ai.agent.id" in attributes, "incomplete Agent Span")
        _require(record["span_kind"] == "INTERNAL", "Agent Span must use INTERNAL kind")
    if operation in {"chat", "generate_content"}:
        _require(
            _set("gen_ai.provider.name gen_ai.request.model") <= set(attributes),
            "incomplete model Span",
        )
        _require(record["span_kind"] == "CLIENT", "model Span must use CLIENT kind")
    if operation == "execute_tool":
        _require(
            _set("gen_ai.tool.name gen_ai.tool.type gen_ai.tool.call.id") <= set(attributes),
            "incomplete tool Span",
        )
        _require(record["span_kind"] == "INTERNAL", "tool Span must use INTERNAL kind")
    standard_by_operation = {
        "invoke_agent": _set(
            "gen_ai.operation.name gen_ai.agent.id gen_ai.agent.name gen_ai.agent.version error.type"
        ),
        "chat": _set(
            "gen_ai.operation.name gen_ai.provider.name gen_ai.request.model gen_ai.response.model gen_ai.usage.input_tokens gen_ai.usage.output_tokens error.type"
        ),
        "generate_content": _set(
            "gen_ai.operation.name gen_ai.provider.name gen_ai.request.model gen_ai.response.model gen_ai.usage.input_tokens gen_ai.usage.output_tokens error.type"
        ),
        "execute_tool": _set(
            "gen_ai.operation.name gen_ai.tool.name gen_ai.tool.type gen_ai.tool.call.id error.type"
        ),
    }
    operation_name = operation if isinstance(operation, str) else ""
    permitted_standard = standard_by_operation.get(operation_name, {"error.type"})
    standard_present = {name for name in attributes if not name.startswith("agentops.")}
    _require(
        not (standard_present - permitted_standard), "standard field prohibited for Span operation"
    )
    if "agentops.model.id" in attributes:
        _require(
            operation in {"chat", "generate_content"}, "model identity outside model-call Span"
        )
        _require(
            _set("agentops.model.id agentops.role.id agentops.runtime.id gen_ai.provider.name")
            <= set(attributes),
            "incomplete model attribution tuple",
        )


def _validate_event(record: dict[str, Any], attributes: dict[str, Any]) -> None:
    event_name = record.get("event_name")
    _require(event_name in EVENT_NAMES, "unknown EventName")
    event_name = cast(str, event_name)
    span_fields = _set(
        "span_name span_kind start_time_unix_nano end_time_unix_nano parent_span_id trace_state span_flags span_links span_status"
    )
    _require(not (span_fields & set(record)), "Span field prohibited on Event")
    if "trace_id" in record:
        _require(
            isinstance(record["trace_id"], str)
            and TRACE_ID.fullmatch(record["trace_id"]) is not None,
            "invalid Event trace_id",
        )
    if "span_id" in record:
        _require(
            isinstance(record["span_id"], str) and SPAN_ID.fullmatch(record["span_id"]) is not None,
            "invalid Event span_id",
        )
    _require(
        not ({name for name in attributes if not name.startswith("agentops.")}),
        "standard Span attribute on Event",
    )
    allowed, required = EVENT_RULES[event_name]
    disallowed = set(attributes) - allowed
    if disallowed:
        raise ValidationError(f"{sorted(disallowed)[0]} prohibited on {event_name}")
    _require(required <= set(attributes), f"incomplete closed field set for {event_name}")
    family = attributes.get("agentops.workflow.family")
    schema = attributes.get("agentops.family.schema")
    if event_name != "sampling.decision":
        _require(
            (family, schema)
            in {("implementation", "implementation@1"), ("system-design", "system-design@1")},
            "family/schema mismatch",
        )
    if event_name == "implementation.summary":
        _require(schema == "implementation@1", "family-specific EventName mismatch")
    if event_name == "system_design.summary":
        _require(schema == "system-design@1", "family-specific EventName mismatch")
    if event_name == "usage" and attributes["agentops.usage.kind"] == "money":
        _require(
            re.fullmatch(r"[A-Z]{3}", attributes["agentops.usage.unit"]) is not None,
            "money usage unit must be ISO-4217 currency",
        )
    if "agentops.review.scope" in attributes:
        _require(
            re.fullmatch(
                r"GOAL:[A-Za-z0-9][A-Za-z0-9._:/@-]{0,122}|WHOLE_SCOPE|SYSTEM_DESIGN",
                attributes["agentops.review.scope"],
            )
            is not None,
            "invalid objective review scope",
        )
    if "agentops.delivery.elapsed_time_ms" in attributes:
        _require(
            attributes["agentops.delivery.elapsed_time_ms"] >= 0, "invalid Delivery elapsed time"
        )
    if event_name == "review.finding":
        _validate_finding(attributes)
    if event_name in {"review.finding", "review.summary"}:
        recheck = "agentops.recheck.id" in attributes
        if recheck:
            _require(
                _set(
                    "agentops.recheck.review.id agentops.iteration.id agentops.recheck.role.id agentops.recheck.invocation.id"
                )
                <= set(attributes),
                "incomplete recheck",
            )
        else:
            _require("agentops.iteration.id" not in attributes, "C27 prohibited outside recheck")
    if event_name == "review.summary":
        fresh_reader_fields = {
            "agentops.fresh_reader.result",
            "agentops.fresh_reader.finding.count",
        }
        if attributes["agentops.review.lens"] == "FRESH_READER":
            _require(
                schema == "system-design@1" and fresh_reader_fields <= set(attributes),
                "incomplete Fresh Reader summary",
            )
        else:
            _require(
                not (fresh_reader_fields & set(attributes)),
                "Fresh Reader field outside Fresh Reader summary",
            )
    if event_name == "implementation.summary":
        _require(
            attributes["agentops.coverage.covered"] <= attributes["agentops.coverage.total"],
            "covered exceeds total",
        )
    probability = attributes.get("agentops.sampling.probability")
    if probability is not None:
        _require(0 <= probability <= 1, "sampling probability outside [0,1]")


def _validate_finding(attributes: dict[str, Any]) -> None:
    kind = attributes["agentops.finding.target.kind"]
    containing = "agentops.finding.target.artifact.id" in attributes
    _require(
        (kind == "SECTION") == containing or kind in {"COMPONENT", "REQUIREMENT"},
        "invalid containing Artifact applicability",
    )
    if kind == "ARTIFACT":
        _require(not containing, "ARTIFACT target prohibits containing Artifact")
    fix = "agentops.fix.id" in attributes
    recheck = "agentops.recheck.id" in attributes
    _require(not (fix and recheck), "Fix and Recheck compositions are mutually exclusive")
    if fix:
        _require(
            attributes.get("agentops.fix.finding.id") == attributes["agentops.finding.id"],
            "incomplete or mismatched fix edge",
        )
    else:
        _require("agentops.fix.finding.id" not in attributes, "incomplete Fix composition")
    if recheck:
        _require(
            attributes.get("agentops.recheck.finding.id") == attributes["agentops.finding.id"],
            "mismatched recheck endpoints",
        )
        _require(
            attributes.get("agentops.recheck.review.id") == attributes["agentops.source.review.id"],
            "mismatched recheck endpoints",
        )
    else:
        recheck_only = _set(
            "agentops.recheck.review.id agentops.recheck.finding.id agentops.recheck.fix.id agentops.recheck.role.id agentops.recheck.invocation.id"
        )
        _require(not (recheck_only & set(attributes)), "incomplete Recheck composition")
    if not fix and not recheck:
        _require(
            attributes["agentops.source.review.id"] == attributes["agentops.review.id"],
            "ordinary Finding source/current Review mismatch",
        )


def validate_record(logical: dict[str, Any]) -> ValidatedRecord:
    _require(isinstance(logical, dict), "record must be an object")
    _validate_envelope(logical)
    attributes = logical["attributes"]
    _validate_attributes(attributes)
    record_type = logical.get("record_type")
    identity: tuple[str, ...]
    if record_type == "span":
        _validate_span(logical, attributes)
        identity = ("span", logical["trace_id"], logical["span_id"])
        event_name = None
    elif record_type == "event":
        _validate_event(logical, attributes)
        identity = ("event", attributes["agentops.event.id"])
        event_name = logical["event_name"]
    else:
        raise ValidationError("invalid record_type")
    return ValidatedRecord(
        logical=logical,
        identity=identity,
        digest=canonical_digest(logical),
        attributes=attributes,
        record_type=record_type,
        event_name=event_name,
    )
