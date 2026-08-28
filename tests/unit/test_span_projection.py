from copy import deepcopy
from typing import Any

import pytest

from wsr_evidence.admission.service import AdmissionService
from wsr_evidence.admission.validation import ValidationError, validate_record


def span_record(
    *, trace_id: str, span_id: str, parent_span_id: str | None = None
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "profile_version": "1.0.0",
        "record_type": "span",
        "span_name": "invoke_workflow delivery",
        "trace_id": trace_id,
        "span_id": span_id,
        "span_kind": "INTERNAL",
        "start_time_unix_nano": "100",
        "end_time_unix_nano": "200",
        "span_flags": 1,
        "span_links": [],
        "span_status": "OK",
        "resource": {"service.name": "dsh", "service.version": "1"},
        "scope": {
            "name": "io.agentops.dsh.observation",
            "version": "1.0.0",
            "schema_url": "https://opentelemetry.io/schemas/1.41.0",
        },
        "attributes": {
            "agentops.delivery.id": "delivery-1",
            "agentops.workflow.id": "workflow-1",
            "agentops.workflow.version": "1.0.0",
            "agentops.implementation.id": "implementation-1",
            "agentops.runtime.id": "runtime-1",
            "agentops.manifest.digest": "b" * 64,
            "agentops.workflow.family": "implementation",
        },
    }
    if parent_span_id is not None:
        record["parent_span_id"] = parent_span_id
    return record


def test_span_identity_is_trace_and_span_tuple() -> None:
    same_span_a = validate_record(span_record(trace_id="1" * 32, span_id="a" * 16))
    same_span_b = validate_record(span_record(trace_id="2" * 32, span_id="a" * 16))

    assert same_span_a.identity != same_span_b.identity


def test_profile_two_non_root_span_requires_direct_delivery_identity() -> None:
    record = span_record(trace_id="1" * 32, span_id="a" * 16)
    record["profile_version"] = "2.0.0"
    record["scope"]["version"] = "2.0.0"
    record["span_name"] = "ordinary child"
    record["attributes"] = {"agentops.delivery.id": "delivery-1"}

    assert validate_record(record).attributes["agentops.delivery.id"] == "delivery-1"
    del record["attributes"]["agentops.delivery.id"]
    with pytest.raises(ValidationError, match="direct Delivery"):
        validate_record(record)


def test_each_span_projects_one_trace_node_without_inferred_causality() -> None:
    record = validate_record(span_record(trace_id="1" * 32, span_id="a" * 16))

    effects = AdmissionService.project(record)

    nodes = [effect for effect in effects if effect.kind == "trace_node"]
    assert len(nodes) == 1
    assert nodes[0].key == ("1" * 32, "a" * 16)
    assert nodes[0].payload["span_flags"] == 1
    assert nodes[0].payload["trace_state"] is None
    assert all(effect.kind != "trace_parent_edge" for effect in effects)


def test_model_attribution_projects_only_the_exact_owner_supplied_tuple() -> None:
    record = span_record(trace_id="1" * 32, span_id="a" * 16)
    record["span_name"] = "chat provider"
    record["span_kind"] = "CLIENT"
    record["attributes"] = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "provider",
        "gen_ai.request.model": "request-alias",
        "agentops.model.id": "canonical-model",
        "agentops.role.id": "implementer",
        "agentops.runtime.id": "runtime-1",
    }

    effects = AdmissionService.project(validate_record(record))
    attribution = next(effect for effect in effects if effect.kind == "model_attribution")

    assert attribution.key == (
        "provider",
        "canonical-model",
        "implementer",
        "runtime-1",
        "1" * 32,
        "a" * 16,
    )
    assert attribution.payload == {"request_model": "request-alias"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("start_time_unix_nano", "01"),
        ("end_time_unix_nano", "not-a-time"),
        ("span_flags", -1),
        ("span_flags", 2**32),
        ("trace_state", "x" * 513),
    ],
)
def test_native_span_fields_enforce_the_frozen_logical_shape(field: str, value: Any) -> None:
    record = span_record(trace_id="1" * 32, span_id="a" * 16)
    record[field] = value

    with pytest.raises(ValidationError):
        validate_record(record)


def test_span_links_are_closed_bounded_native_edges() -> None:
    record = span_record(trace_id="1" * 32, span_id="a" * 16)
    record["span_links"] = [
        {"trace_id": "2" * 32, "span_id": "b" * 16, "flags": 1, "unknown": "no"}
    ]

    with pytest.raises(ValidationError, match="Span link"):
        validate_record(record)


def test_event_may_carry_trace_correlation_but_keeps_event_identity() -> None:
    record = deepcopy(span_record(trace_id="1" * 32, span_id="a" * 16))
    record["record_type"] = "event"
    record["event_name"] = "sampling.decision"
    record["attributes"] = {
        "agentops.event.id": "event-1",
        "agentops.sampling.decision": "DROP",
        "agentops.sampling.probability": 0.0,
    }
    for field in (
        "span_name",
        "span_kind",
        "start_time_unix_nano",
        "end_time_unix_nano",
        "span_flags",
        "span_links",
        "span_status",
    ):
        record.pop(field)

    validated = validate_record(record)

    assert validated.identity == ("event", "event-1")
    assert validated.logical["trace_id"] == "1" * 32
    assert validated.logical["span_id"] == "a" * 16

    malformed = deepcopy(record)
    malformed["trace_id"] = "not-a-trace"
    with pytest.raises(ValidationError, match="trace_id"):
        validate_record(malformed)
