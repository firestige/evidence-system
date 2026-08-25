from typing import Any

from wsr_evidence.admission.service import AdmissionService
from wsr_evidence.admission.validation import validate_record


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


def test_each_span_projects_one_trace_node_without_inferred_causality() -> None:
    record = validate_record(span_record(trace_id="1" * 32, span_id="a" * 16))

    effects = AdmissionService.project(record)

    nodes = [effect for effect in effects if effect.kind == "trace_node"]
    assert len(nodes) == 1
    assert nodes[0].key == ("1" * 32, "a" * 16)
    assert all(effect.kind != "trace_parent_edge" for effect in effects)
