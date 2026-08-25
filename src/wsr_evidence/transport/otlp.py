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
from wsr_evidence.admission.validation import ValidationError
from wsr_evidence.model import Disposition

MAX_BATCH_BYTES = 4 * 1024 * 1024
MAX_BATCH_RECORDS = 512


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


def _envelope(resource: Any, scope: Any, schema_url: str) -> dict[str, Any]:
    return {
        "profile_version": scope.version,
        "resource": _attributes(resource.attributes),
        "scope": {"name": scope.name, "version": scope.version, "schema_url": schema_url},
    }


def decode_logs_request(payload: bytes) -> list[dict[str, Any]]:
    request = ExportLogsServiceRequest()
    _parse(request, payload)
    records: list[dict[str, Any]] = []
    for resource_logs in request.resource_logs:
        for scope_logs in resource_logs.scope_logs:
            envelope = _envelope(resource_logs.resource, scope_logs.scope, scope_logs.schema_url)
            for log_record in scope_logs.log_records:
                if log_record.body.WhichOneof("value") is not None:
                    raise OtlpDecodeError("OTLP LogRecord body must be empty")
                logical = {
                    **envelope,
                    "record_type": "event",
                    "event_name": log_record.event_name,
                    "attributes": _attributes(log_record.attributes),
                }
                if log_record.trace_id:
                    logical["trace_id"] = log_record.trace_id.hex()
                if log_record.span_id:
                    logical["span_id"] = log_record.span_id.hex()
                records.append(logical)
    _check_record_count(records)
    return records


def decode_traces_request(payload: bytes) -> list[dict[str, Any]]:
    request = ExportTraceServiceRequest()
    _parse(request, payload)
    records: list[dict[str, Any]] = []
    for resource_spans in request.resource_spans:
        for scope_spans in resource_spans.scope_spans:
            envelope = _envelope(resource_spans.resource, scope_spans.scope, scope_spans.schema_url)
            for span in scope_spans.spans:
                links = []
                for link in span.links:
                    if link.attributes:
                        raise OtlpDecodeError("Span Link attributes are not in Profile 1.0.0")
                    links.append(
                        {
                            "trace_id": link.trace_id.hex(),
                            "span_id": link.span_id.hex(),
                            **({"trace_state": link.trace_state} if link.trace_state else {}),
                            **({"flags": link.flags} if link.flags else {}),
                        }
                    )
                logical = {
                    **envelope,
                    "record_type": "span",
                    "span_name": span.name,
                    "trace_id": span.trace_id.hex(),
                    "span_id": span.span_id.hex(),
                    "span_kind": Span.SpanKind.Name(span.kind).removeprefix("SPAN_KIND_"),
                    "start_time_unix_nano": str(span.start_time_unix_nano),
                    "end_time_unix_nano": str(span.end_time_unix_nano),
                    "span_flags": span.flags,
                    "span_links": links,
                    "span_status": Status.StatusCode.Name(span.status.code).removeprefix(
                        "STATUS_CODE_"
                    ),
                    "attributes": _attributes(span.attributes),
                }
                if span.parent_span_id:
                    logical["parent_span_id"] = span.parent_span_id.hex()
                if span.trace_state:
                    logical["trace_state"] = span.trace_state
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
        if not _homogeneous(records):
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


def create_otlp_router(ingestor: OtlpIngestor) -> APIRouter:
    router = APIRouter()

    async def ingest(request: Request, signal: Literal["logs", "traces"]) -> Response:
        if request.headers.get("content-type") != "application/x-protobuf":
            return Response(
                _status_payload("Content-Type must be application/x-protobuf"),
                status_code=400,
                media_type="application/x-protobuf",
            )
        try:
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
