"""OTLP/HTTP protobuf decoding and aggregate admission outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

from fastapi import APIRouter, Request, Response
from google.protobuf.message import DecodeError, Message
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceRequest,
    ExportLogsServiceResponse,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import Span, Status

from wsr_evidence.admission.service import AdmissionService
from wsr_evidence.admission.validation import ValidationError, canonical_bytes
from wsr_evidence.model import Disposition

MAX_BATCH_BYTES = 4 * 1024 * 1024
MAX_BATCH_RECORDS = 512
BOUNDED_CARDINALITY_FIELDS = {
    "agentops.workflow.id",
    "agentops.workflow.version",
    "agentops.implementation.id",
    "agentops.runtime.id",
    "agentops.review.scope",
    "agentops.review.total",
    "agentops.review.observed.count",
    "agentops.observed.loop.count",
    "agentops.observed.intervention.count",
    "agentops.usage.unit",
    "agentops.usage.source.id",
    "agentops.usage.value",
    "agentops.finding.summary",
    "agentops.delivery.elapsed_time_ms",
    "agentops.delivery.stage.reached",
    "agentops.model.id",
    "agentops.test.passed",
    "agentops.test.failed",
    "agentops.test.skipped",
    "agentops.test.duration.seconds",
    "agentops.coverage.covered",
    "agentops.coverage.total",
    "agentops.coverage.tool.id",
    "agentops.coverage.format",
    "agentops.fresh_reader.finding.count",
    "agentops.verification.check.passed",
    "agentops.verification.check.failed",
}


class OtlpDecodeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class OtlpOutcome:
    signal: Literal["logs", "traces"]
    http_status: int
    rejected_items: int
    dispositions: None = None


def _scalar(value: AnyValue) -> str | int | float | bool:
    selected = value.WhichOneof("value")
    if selected == "string_value":
        return value.string_value
    if selected == "int_value":
        return value.int_value
    if selected == "double_value":
        return value.double_value
    if selected == "bool_value":
        return value.bool_value
    raise OtlpDecodeError("OTLP attributes must use scalar AnyValue carriers")


def _attributes(values: Any) -> dict[str, str | int | float | bool]:
    result: dict[str, str | int | float | bool] = {}
    for item in values:
        key_value: KeyValue = item
        if key_value.key in result:
            raise OtlpDecodeError(f"duplicate OTLP attribute {key_value.key}")
        result[key_value.key] = _scalar(key_value.value)
    return result


def _parse(message: Message, payload: bytes) -> None:
    if len(payload) > MAX_BATCH_BYTES:
        raise OtlpDecodeError("OTLP protobuf request exceeds batch byte limit")
    try:
        message.ParseFromString(payload)
    except DecodeError as error:
        raise OtlpDecodeError("invalid OTLP protobuf") from error


def _envelope(resource: Any, scope: Any, schema_url: str) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    try:
        resource_attributes = _attributes(resource.attributes)
    except OtlpDecodeError as error:
        resource_attributes = {}
        errors.append(str(error))
    if resource.dropped_attributes_count:
        errors.append("dropped Resource attributes")
    if scope.attributes or scope.dropped_attributes_count:
        errors.append("InstrumentationScope attributes")
    return (
        {
            "profile_version": scope.version,
            "resource": resource_attributes,
            "scope": {"name": scope.name, "version": scope.version, "schema_url": schema_url},
        },
        errors,
    )


def _decoded_attributes(values: Any, *, dropped: int) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    try:
        attributes = _attributes(values)
    except OtlpDecodeError as error:
        attributes = {}
        errors.append(str(error))
    if dropped:
        errors.append("dropped record attributes")
    return attributes, errors


def _mark_carrier_errors(logical: dict[str, Any], errors: list[str]) -> None:
    if errors:
        logical["_carrier_error"] = "; ".join(errors)


def _native_enum_name(enum: Any, value: int, label: str, prefix: str) -> str:
    try:
        return str(enum.Name(value)).removeprefix(prefix)
    except ValueError as error:
        raise OtlpDecodeError(f"unsupported native {label}") from error


def decode_logs_request(payload: bytes) -> list[dict[str, Any]]:
    request = ExportLogsServiceRequest()
    _parse(request, payload)
    records: list[dict[str, Any]] = []
    for resource_logs in request.resource_logs:
        for scope_logs in resource_logs.scope_logs:
            envelope, group_errors = _envelope(
                resource_logs.resource, scope_logs.scope, scope_logs.schema_url
            )
            for log_record in scope_logs.log_records:
                attributes, attribute_errors = _decoded_attributes(
                    log_record.attributes, dropped=log_record.dropped_attributes_count
                )
                errors = [*group_errors, *attribute_errors]
                if log_record.body.WhichOneof("value") is not None:
                    errors.append("OTLP LogRecord body must be empty")
                logical = {
                    **envelope,
                    "record_type": "event",
                    "event_name": log_record.event_name,
                    "attributes": attributes,
                }
                if log_record.trace_id:
                    logical["trace_id"] = log_record.trace_id.hex()
                if log_record.span_id:
                    logical["span_id"] = log_record.span_id.hex()
                _mark_carrier_errors(logical, errors)
                records.append(logical)
    _check_record_count(records)
    return records


def decode_traces_request(payload: bytes) -> list[dict[str, Any]]:
    request = ExportTraceServiceRequest()
    _parse(request, payload)
    records: list[dict[str, Any]] = []
    for resource_spans in request.resource_spans:
        for scope_spans in resource_spans.scope_spans:
            envelope, group_errors = _envelope(
                resource_spans.resource, scope_spans.scope, scope_spans.schema_url
            )
            for span in scope_spans.spans:
                attributes, attribute_errors = _decoded_attributes(
                    span.attributes, dropped=span.dropped_attributes_count
                )
                errors = [*group_errors, *attribute_errors]
                if span.events or span.dropped_events_count:
                    errors.append("Span Events are outside Profile 1.0.0")
                if span.dropped_links_count:
                    errors.append("dropped Span Links")
                if span.status.message:
                    errors.append("Span Status message is prohibited content")
                links = []
                for link in span.links:
                    if link.attributes or link.dropped_attributes_count:
                        errors.append("Span Link attributes are outside Profile 1.0.0")
                        continue
                    links.append(
                        {
                            "trace_id": link.trace_id.hex(),
                            "span_id": link.span_id.hex(),
                            **({"trace_state": link.trace_state} if link.trace_state else {}),
                            **({"flags": link.flags} if link.flags else {}),
                        }
                    )
                try:
                    span_kind = _native_enum_name(
                        Span.SpanKind, span.kind, "Span kind", "SPAN_KIND_"
                    )
                except OtlpDecodeError as error:
                    span_kind = "INVALID"
                    errors.append(str(error))
                try:
                    span_status = _native_enum_name(
                        Status.StatusCode,
                        span.status.code,
                        "Span status",
                        "STATUS_CODE_",
                    )
                except OtlpDecodeError as error:
                    span_status = "INVALID"
                    errors.append(str(error))
                logical = {
                    **envelope,
                    "record_type": "span",
                    "span_name": span.name,
                    "trace_id": span.trace_id.hex(),
                    "span_id": span.span_id.hex(),
                    "span_kind": span_kind,
                    "start_time_unix_nano": str(span.start_time_unix_nano),
                    "end_time_unix_nano": str(span.end_time_unix_nano),
                    "span_flags": span.flags,
                    "span_links": links,
                    "span_status": span_status,
                    "attributes": attributes,
                }
                if span.parent_span_id:
                    logical["parent_span_id"] = span.parent_span_id.hex()
                if span.trace_state:
                    logical["trace_state"] = span.trace_state
                _mark_carrier_errors(logical, errors)
                records.append(logical)
    _check_record_count(records)
    return records


def _check_record_count(records: list[dict[str, Any]]) -> None:
    if len(records) > MAX_BATCH_RECORDS:
        raise OtlpDecodeError("OTLP protobuf request exceeds record limit")


def _homogeneous(records: list[dict[str, Any]]) -> bool:
    schemas = {
        record["attributes"].get("agentops.family.schema")
        for record in records
        if record["attributes"].get("agentops.family.schema") is not None
    }
    return len(schemas) <= 1


def _within_cardinality_budget(records: list[dict[str, Any]]) -> bool:
    for field in BOUNDED_CARDINALITY_FIELDS:
        values = {
            canonical_bytes(record["attributes"][field])
            for record in records
            if field in record["attributes"]
        }
        if len(values) > 256:
            return False
    return True


class OtlpIngestor:
    def __init__(self, admission: AdmissionService) -> None:
        self._admission = admission

    async def ingest_logs(self, payload: bytes) -> OtlpOutcome:
        return await self._ingest("logs", decode_logs_request(payload))

    async def ingest_traces(self, payload: bytes) -> OtlpOutcome:
        return await self._ingest("traces", decode_traces_request(payload))

    async def _ingest(
        self, signal: Literal["logs", "traces"], records: list[dict[str, Any]]
    ) -> OtlpOutcome:
        if not _homogeneous(records) or not _within_cardinality_budget(records):
            return OtlpOutcome(signal, 400, len(records))
        rejected = 0
        for record in records:
            try:
                result = await self._admission.admit(record)
            except ValidationError:
                rejected += 1
                continue
            if result.disposition in {Disposition.CONFLICT, Disposition.REJECTED}:
                rejected += 1
        status = 400 if records and rejected == len(records) else 200
        return OtlpOutcome(signal, status, rejected)


def _varint(value: int) -> bytes:
    output = bytearray()
    while value > 0x7F:
        output.append((value & 0x7F) | 0x80)
        value >>= 7
    output.append(value)
    return bytes(output)


def _status_payload(message: str) -> bytes:
    encoded = message.encode()[:256]
    return b"\x08\x03\x12" + _varint(len(encoded)) + encoded


def _export_payload(outcome: OtlpOutcome) -> bytes:
    if outcome.signal == "logs":
        response = ExportLogsServiceResponse()
        if outcome.rejected_items:
            response.partial_success.rejected_log_records = outcome.rejected_items
            response.partial_success.error_message = "one or more log records rejected"
    else:
        response = ExportTraceServiceResponse()
        if outcome.rejected_items:
            response.partial_success.rejected_spans = outcome.rejected_items
            response.partial_success.error_message = "one or more spans rejected"
    return cast(bytes, response.SerializeToString())


def create_otlp_router() -> APIRouter:
    router = APIRouter()

    async def ingest(request: Request, signal: Literal["logs", "traces"]) -> Response:
        if request.headers.get("content-type") != "application/x-protobuf":
            return Response(
                _status_payload("Content-Type must be application/x-protobuf"),
                status_code=400,
                media_type="application/x-protobuf",
            )
        try:
            ingestor: OtlpIngestor | None = getattr(request.app.state, "otlp_ingestor", None)
            if ingestor is None:
                return Response(
                    _status_payload("Evidence storage is unavailable"),
                    status_code=503,
                    media_type="application/x-protobuf",
                )
            payload = await request.body()
            outcome = (
                await ingestor.ingest_logs(payload)
                if signal == "logs"
                else await ingestor.ingest_traces(payload)
            )
        except OtlpDecodeError:
            return Response(
                _status_payload("invalid OTLP protobuf request"),
                status_code=400,
                media_type="application/x-protobuf",
            )
        response_payload = (
            _status_payload("all logical records rejected")
            if outcome.http_status == 400
            else _export_payload(outcome)
        )
        return Response(
            response_payload,
            status_code=outcome.http_status,
            media_type="application/x-protobuf",
        )

    @router.post("/v1/logs")
    async def ingest_logs(request: Request) -> Response:
        return await ingest(request, "logs")

    @router.post("/v1/traces")
    async def ingest_traces(request: Request) -> Response:
        return await ingest(request, "traces")

    return router
