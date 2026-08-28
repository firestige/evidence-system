import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from wsr_evidence.admission.validation import EVENT_NAMES as ADMISSION_EVENT_NAMES
from wsr_evidence.app import create_app
from wsr_evidence.query.faults import SnapshotError, SnapshotFault
from wsr_evidence.query.model import QueryEffect
from wsr_evidence.query.postgresql import PostgresQueryReadModel
from wsr_evidence.query.service import (
    EVENT_NAMES,
    WAVE6_INPUT_MANIFEST_SHA256,
    QueryError,
    QueryErrorCode,
    QueryService,
)
from wsr_evidence.storage.read_model import (
    ExpiryRecord,
    ResourceClass,
    RetentionPolicy,
    SnapshotPage,
    TraceDetailState,
    TraceSummary,
)

MANIFEST_DIGEST = "e605720c5b225fa9228e2a4b1a8001f3235482ed83dc214e4c766e5caa6e1706"
GOLDEN = Path(__file__).parents[1] / "fixtures" / "wave7_fact_response.json"


@pytest.mark.parametrize(
    "configuration",
    [
        {"lease_ttl": timedelta(seconds=9)},
        {"lease_ttl": timedelta(seconds=301)},
        {"lease_ttl": timedelta(seconds=10, microseconds=1)},
        {"lease_limit": 0},
        {"lease_limit": 9},
    ],
)
def test_snapshot_configuration_enforces_published_ranges(configuration: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        PostgresQueryReadModel(None, **configuration)  # type: ignore[arg-type]


def review_summary(*, observed_count: int | None, state: str | None = "FINAL") -> QueryEffect:
    attributes: dict[str, str | int] = {
        "agentops.event.id": "review-event-1",
        "agentops.family.schema": "system-design@1",
        "agentops.review.id": "review-1",
        "agentops.review.lens": "FRESH_READER",
        "agentops.review.scope": "SYSTEM_DESIGN",
    }
    if state is not None:
        attributes["agentops.summary.state"] = state
    if observed_count is not None:
        attributes["agentops.review.observed.count"] = observed_count
    return QueryEffect(
        kind="factual_contribution",
        key=("review.summary", "review-event-1"),
        payload={
            "attributes": attributes,
            "compatibility_key": (
                "system-design@1",
                "review.summary",
                state,
                "FRESH_READER",
                "SYSTEM_DESIGN",
            ),
            "aggregate_eligible": True,
        },
        source_identity=("event", '["event","review-event-1"]'),
        recorded_at=datetime(2026, 8, 26, 1, 2, 3, tzinfo=UTC),
        accepted_digest="a" * 64,
        profile_version="1.0.0",
        family_schema="system-design@1",
    )


class FakeReadModel:
    def __init__(
        self,
        resources: tuple[QueryEffect, ...],
        *,
        expiry: ExpiryRecord | None = None,
        expiries: dict[tuple[object, ...], ExpiryRecord] | None = None,
        trace_summaries: tuple[TraceSummary, ...] | None = None,
        continuation_fault: SnapshotFault | None = None,
    ) -> None:
        self.resources = resources
        self.expiry = expiry
        self.expiries = expiries or {}
        self.trace_summaries = trace_summaries
        self.continuation_fault = continuation_fault
        self.released: list[str] = []
        self.acquired: list[tuple[str, tuple[tuple[str, str], ...], int]] = []
        self.last_query = "FACTS"

    async def acquire_snapshot(
        self,
        *,
        query: str,
        filters: tuple[tuple[str, str], ...],
        limit: int,
        clock_now: datetime,
    ) -> SnapshotPage[QueryEffect]:
        del clock_now
        self.acquired.append((query, filters, limit))
        self.last_query = query
        return SnapshotPage(
            contract_revision="1.0.0" if query == "TASKS" else "0.1.0",
            read_model_revision="2.0.0" if query == "TASKS" else "1.0.0",
            snapshot_id="snapshot-1",
            resources=self.resources,
            next_cursor=None,
        )

    async def continue_snapshot(
        self, *, cursor: str, clock_now: datetime
    ) -> SnapshotPage[QueryEffect]:
        if self.continuation_fault is not None:
            raise SnapshotError(self.continuation_fault, "bounded storage fault")
        del cursor, clock_now
        return await self.acquire_snapshot(
            query=self.last_query, filters=(), limit=100, clock_now=datetime.now(UTC)
        )

    async def read_expiry(self, **kwargs: object) -> ExpiryRecord | None:
        owner_key = kwargs["owner_key"]
        assert isinstance(owner_key, tuple)
        return self.expiries.get(owner_key, self.expiry)

    async def summarize_traces(self, *, snapshot_id: str) -> tuple[TraceSummary, ...]:
        del snapshot_id
        if self.trace_summaries is not None:
            return self.trace_summaries
        trace_ids = sorted({str(resource.key[0]) for resource in self.resources})
        return tuple(
            TraceSummary(trace_id=trace_id, state=TraceDetailState.AVAILABLE)
            for trace_id in trace_ids
        )

    async def release_snapshot(self, snapshot_id: str) -> None:
        self.released.append(snapshot_id)


def task_effect(
    *,
    kind: str,
    key: tuple[str, ...],
    payload: dict[str, str],
    event_id: str = "task-event-1",
    recorded_at: datetime = datetime(2026, 8, 26, 1, 2, 3, tzinfo=UTC),
) -> QueryEffect:
    return QueryEffect(
        kind=kind,
        key=key,
        payload=payload,
        source_identity=("event", json.dumps(["event", event_id], separators=(",", ":"))),
        recorded_at=recorded_at,
        accepted_digest="b" * 64,
        profile_version="2.0.0",
        family_schema=None,
    )


@pytest.mark.asyncio
async def test_task_list_preserves_id_identity_and_optional_display_name() -> None:
    read_model = FakeReadModel(
        (
            task_effect(
                kind="task_declaration",
                key=("task-a",),
                payload={"display_name": "Agent 配置基线"},
            ),
            task_effect(kind="task_declaration", key=("task-b",), payload={}),
        )
    )

    response = await QueryService(read_model).tasks({})

    assert response["contract"] == {"name": "evidence.query", "revision": "1.0.0"}
    assert response["observation_profile"] == "2.0.0"
    assert response["read_model_revision"] == "2.0.0"
    assert [(item["task_id"], item["display_name"]) for item in response["items"]] == [
        ("task-a", "Agent 配置基线"),
        ("task-b", None),
    ]
    assert response["items"][0]["provenance"] == {
        "accepted_digest": "b" * 64,
        "profile_version": "2.0.0",
        "recorded_at": "2026-08-26T01:02:03.000000Z",
        "source": {"kind": "EVENT", "event_id": "task-event-1"},
    }
    assert read_model.released == ["snapshot-1"]


@pytest.mark.asyncio
async def test_task_membership_requires_exact_task_and_normalized_as_of() -> None:
    membership = task_effect(
        kind="delivery_task_membership",
        key=("task-a", "delivery-2"),
        payload={"manifest_digest": MANIFEST_DIGEST},
    )
    read_model = FakeReadModel((membership,))

    response = await QueryService(read_model).tasks(
        {"task_id": "task-a", "as_of": "2026-08-26T01:02:04Z"}
    )

    assert response["items"][0] == {
        "task_id": "task-a",
        "delivery_id": "delivery-2",
        "manifest_digest": MANIFEST_DIGEST,
        "recorded_at": "2026-08-26T01:02:03.000000Z",
        "provenance": {
            "accepted_digest": "b" * 64,
            "profile_version": "2.0.0",
            "source": {"kind": "EVENT", "event_id": "task-event-1"},
        },
    }
    assert read_model.acquired == [
        (
            "TASKS",
            (
                ("as_of", "2026-08-26T01:02:04.000000Z"),
                ("limit", "100"),
                ("task_id", "task-a"),
            ),
            100,
        )
    ]

    for parameters in ({"task_id": "task-a"}, {"as_of": "2026-08-26T01:02:04Z"}):
        with pytest.raises(QueryError) as caught:
            await QueryService(read_model).tasks(parameters)
        assert caught.value.code is QueryErrorCode.INVALID_FILTER

    for invalid_task_id in ("a" * 129, " task-a", "task?a"):
        with pytest.raises(QueryError) as caught:
            await QueryService(read_model).tasks(
                {"task_id": invalid_task_id, "as_of": "2026-08-26T01:02:04Z"}
            )
        assert caught.value.code is QueryErrorCode.INVALID_FILTER


@pytest.mark.asyncio
async def test_http_task_query_is_read_only_and_rejects_repeated_filters() -> None:
    service = QueryService(FakeReadModel(()))
    transport = ASGITransport(app=create_app(query_service=service))
    async with AsyncClient(transport=transport, base_url="http://evidence.test") as client:
        success = await client.get("/v1/evidence/tasks")
        repeated = await client.get(
            "/v1/evidence/tasks?task_id=a&task_id=b&as_of=2026-08-26T01:02:04Z"
        )
        body = await client.request("GET", "/v1/evidence/tasks", content=b"not-allowed")
        write = await client.post("/v1/evidence/tasks", json={})

    assert success.status_code == 200
    assert repeated.status_code == 400
    assert repeated.json()["error"]["code"] == "INVALID_FILTER"
    assert body.status_code == 400
    assert write.status_code == 405


@pytest.mark.asyncio
async def test_fact_query_preserves_explicit_zero_and_manifest_binding() -> None:
    read_model = FakeReadModel((review_summary(observed_count=0),))
    service = QueryService(read_model)

    response = await service.facts({})

    assert WAVE6_INPUT_MANIFEST_SHA256 == MANIFEST_DIGEST
    assert EVENT_NAMES == ADMISSION_EVENT_NAMES
    assert response["contract"] == {"name": "evidence.query", "revision": "0.1.0"}
    item = response["items"][0]
    assert item["kind"] == "EVENT_CONTRIBUTION"
    assert item["truth"] == {
        "completeness": "FINAL",
        "availability": "AVAILABLE",
        "expiry": "ACTIVE",
        "expires_at": "2027-08-26T01:02:03.000000Z",
    }
    assert {field["field"]: field["value"] for field in item["fields"]}["C17"] == 0
    assert response == json.loads(GOLDEN.read_text())
    assert read_model.released == ["snapshot-1"]


@pytest.mark.asyncio
async def test_query_expiry_instant_uses_the_configured_physical_ttl() -> None:
    service = QueryService(
        FakeReadModel((review_summary(observed_count=0),)),
        retention_policy=RetentionPolicy(factual_projection_ttl=timedelta(days=90)),
    )

    response = await service.facts({})

    assert response["items"][0]["truth"]["expires_at"] == "2026-11-24T01:02:03.000000Z"


@pytest.mark.asyncio
async def test_raw_debug_scrub_does_not_change_projected_event_fact() -> None:
    effect = review_summary(observed_count=0)

    response = await QueryService(FakeReadModel((effect,))).facts({})

    assert response["items"][0]["compatibility"] == {
        "family_schema": "system-design@1",
        "event_name": "review.summary",
        "completeness": "FINAL",
        "dimensions": [
            {"field": "C13", "value": "FRESH_READER"},
            {"field": "C14", "value": "SYSTEM_DESIGN"},
        ],
    }


@pytest.mark.asyncio
async def test_absent_c17_produces_no_observed_count_field() -> None:
    service = QueryService(FakeReadModel((review_summary(observed_count=None),)))

    response = await service.facts({})

    assert "C17" not in {field["field"] for field in response["items"][0]["fields"]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "availability", "preserves_zero"),
    [
        ("LOWER_BOUND", "AVAILABLE", True),
        ("NOT_APPLICABLE", "AVAILABLE", False),
        ("UNAVAILABLE", "UNAVAILABLE", False),
        (None, "AVAILABLE", True),
    ],
)
async def test_active_completeness_truth_table(
    state: str | None, availability: str, preserves_zero: bool
) -> None:
    service = QueryService(FakeReadModel((review_summary(observed_count=0, state=state),)))

    response = await service.facts({})

    item = response["items"][0]
    assert item["truth"]["completeness"] == state
    assert item["truth"]["availability"] == availability
    assert item["truth"]["expiry"] == "ACTIVE"
    fields = {field["field"]: field["value"] for field in item["fields"]}
    assert (fields.get("C17") == 0) is preserves_zero


@pytest.mark.asyncio
async def test_expired_detail_is_unavailable_not_absent() -> None:
    effect = review_summary(observed_count=7)
    expiry = ExpiryRecord(
        resource_class=ResourceClass.FACTUAL_PROJECTION,
        owner_key=effect.key,
        source_identity=effect.source_identity,
        resource_kind="EVENT_CONTRIBUTION",
        recorded_at=effect.recorded_at,
        compatibility=(
            ("family_schema", "system-design@1"),
            ("event_name", "review.summary"),
            ("completeness", "FINAL"),
            ("C13", "FRESH_READER"),
            ("C14", "SYSTEM_DESIGN"),
        ),
        policy_revision="1.0.0",
        expires_at=datetime(2027, 8, 26, 1, 2, 3, tzinfo=UTC),
        expired_at=datetime(2027, 8, 27, tzinfo=UTC),
    )
    service = QueryService(FakeReadModel((effect,), expiry=expiry))

    response = await service.facts({})

    item = response["items"][0]
    assert item["fields"] == []
    assert item["truth"] == {
        "completeness": "FINAL",
        "availability": "UNAVAILABLE",
        "expiry": "EXPIRED",
        "expires_at": "2027-08-26T01:02:03.000000Z",
    }


@pytest.mark.asyncio
async def test_trace_query_returns_only_recorded_node_and_link_without_inference() -> None:
    trace_id = "1" * 32
    span_id = "a" * 16
    linked_trace = "2" * 32
    linked_span = "b" * 16
    common = {
        "source_identity": ("span", f'["span","{trace_id}","{span_id}"]'),
        "recorded_at": datetime(2026, 8, 26, 1, 2, 3, tzinfo=UTC),
        "accepted_digest": "b" * 64,
        "profile_version": "1.0.0",
        "family_schema": None,
    }
    node = QueryEffect(
        kind="trace_node",
        key=(trace_id, span_id),
        payload={
            "span_name": "recorded",
            "span_kind": "INTERNAL",
            "start_time_unix_nano": "1",
            "end_time_unix_nano": "2",
            "span_status": "OK",
            "span_flags": 1,
            "trace_state": None,
            "attributes": {
                "gen_ai.request.model": "requested",
                "agentops.model.id": "canonical",
                "agentops.runtime.id": "runtime",
                "gen_ai.operation.name": "chat",
                "agentops.role.id": "worker",
                "gen_ai.provider.name": "provider",
                "arbitrary": "hidden",
            },
        },
        **common,
    )
    link = QueryEffect(
        kind="trace_link",
        key=(trace_id, span_id, linked_trace, linked_span),
        payload={
            "trace_id": linked_trace,
            "span_id": linked_span,
            "trace_state": "vendor=exact",
            "flags": 1,
        },
        **common,
    )
    service = QueryService(FakeReadModel((node, link)))

    response = await service.traces({"trace_id": trace_id})

    assert response["trace_state"] == "AVAILABLE"
    assert response["trace_summaries"] == [{"trace_id": trace_id, "state": "AVAILABLE"}]
    assert [item["kind"] for item in response["items"]] == ["NODE", "LINK"]
    assert response["items"][0]["node"]["fields"] == [
        {"field": "C06", "value": "runtime"},
        {"field": "C30", "value": "worker"},
        {"field": "C57", "value": "canonical"},
        {"field": "gen_ai.operation.name", "value": "chat"},
        {"field": "gen_ai.provider.name", "value": "provider"},
        {"field": "gen_ai.request.model", "value": "requested"},
    ]
    assert response["items"][0]["edge"] is None
    assert response["items"][1]["node"] is None
    assert response["items"][1]["edge"] == {
        "from": {"trace_id": trace_id, "span_id": span_id},
        "to": {"trace_id": linked_trace, "span_id": linked_span},
        "trace_state": "vendor=exact",
        "flags": 1,
    }


@pytest.mark.asyncio
async def test_trace_query_reports_partial_detail_without_reconstruction() -> None:
    trace_id = "1" * 32
    span_id = "a" * 16
    parent_id = "b" * 16
    common = {
        "source_identity": ("span", f'["span","{trace_id}","{span_id}"]'),
        "recorded_at": datetime(2026, 8, 26, 1, 2, 3, tzinfo=UTC),
        "accepted_digest": "b" * 64,
        "profile_version": "1.0.0",
        "family_schema": None,
    }
    node = QueryEffect(
        kind="trace_node",
        key=(trace_id, span_id),
        payload={
            "span_name": "recorded",
            "span_kind": "INTERNAL",
            "start_time_unix_nano": "1",
            "end_time_unix_nano": "2",
            "span_status": "OK",
            "span_flags": 1,
            "trace_state": None,
            "attributes": {},
        },
        **common,
    )
    parent = QueryEffect(
        kind="trace_parent_edge",
        key=(trace_id, span_id, parent_id),
        payload={},
        **common,
    )
    expiry = ExpiryRecord(
        resource_class=ResourceClass.TRACE_DETAIL,
        owner_key=parent.key,
        source_identity=parent.source_identity,
        resource_kind="PARENT_EDGE",
        recorded_at=parent.recorded_at,
        compatibility=(),
        policy_revision="1.0.0",
        expires_at=datetime(2026, 9, 25, 1, 2, 3, tzinfo=UTC),
        expired_at=datetime(2026, 9, 26, tzinfo=UTC),
    )
    service = QueryService(
        FakeReadModel(
            (node, parent),
            expiries={parent.key: expiry},
            trace_summaries=(TraceSummary(trace_id=trace_id, state=TraceDetailState.PARTIAL),),
        )
    )

    response = await service.traces({"trace_id": trace_id})

    assert response["trace_state"] == "PARTIAL"
    assert response["trace_summaries"] == [{"trace_id": trace_id, "state": "PARTIAL"}]
    assert [item["kind"] for item in response["items"]] == ["NODE"]


@pytest.mark.asyncio
async def test_relationship_fact_does_not_leak_unowned_source_fields() -> None:
    effect = QueryEffect(
        kind="finding_status",
        key=("finding-1", "scope-1", "review-1"),
        payload={
            "status": "OPEN",
            "writer_role_id": "writer",
            "writer_invocation_id": "writer-invocation",
            "reviewer_role_id": "reviewer",
            "reviewer_invocation_id": "reviewer-invocation",
        },
        source_identity=("event", '["event","finding-event-1"]'),
        recorded_at=datetime(2026, 8, 26, 1, 2, 3, tzinfo=UTC),
        accepted_digest="c" * 64,
        profile_version="1.0.0",
        family_schema="system-design@1",
    )

    response = await QueryService(FakeReadModel((effect,))).facts({})

    item = response["items"][0]
    assert item["compatibility"]["event_name"] is None
    assert {field["field"] for field in item["fields"]} == {
        "C12",
        "C18",
        "C19",
        "C51",
        "C33",
        "C34",
        "C36",
        "C37",
    }


@pytest.mark.asyncio
async def test_finding_target_relationship_has_typed_exact_endpoints() -> None:
    effect = QueryEffect(
        kind="finding_target",
        key=("finding-1", "scope-1", "SECTION", "section-1", "artifact-1"),
        payload={},
        source_identity=("event", '["event","finding-event-1"]'),
        recorded_at=datetime(2026, 8, 26, 1, 2, 3, tzinfo=UTC),
        accepted_digest="c" * 64,
        profile_version="1.0.0",
        family_schema="system-design@1",
    )

    response = await QueryService(FakeReadModel((effect,))).facts({})

    assert response["items"][0]["relationships"] == [
        {
            "kind": "FINDING_TARGET",
            "from": {"kind": "FINDING", "key": ["finding-1", "scope-1"]},
            "to": {
                "kind": "FINDING_TARGET",
                "key": ["finding-1", "scope-1", "SECTION", "section-1", "artifact-1"],
            },
        }
    ]


@pytest.mark.asyncio
async def test_recheck_projection_exposes_only_its_closed_owned_fields() -> None:
    effect = QueryEffect(
        kind="finding_recheck",
        key=("finding-1", "scope-1", "SECTION", "section-1", "artifact-1", "recheck-1"),
        payload={
            "review_id": "review-current",
            "prior_review_id": "review-source",
            "finding_id": "finding-1",
            "fix_id": "fix-1",
            "iteration_id": "iteration-1",
            "writer_role_id": "writer",
            "writer_invocation_id": "writer-call",
            "reviewer_role_id": "reviewer",
            "reviewer_invocation_id": "reviewer-call",
            "recheck_role_id": "rechecker",
            "recheck_invocation_id": "recheck-call",
        },
        source_identity=("event", '["event","recheck-event-1"]'),
        recorded_at=datetime(2026, 8, 26, 1, 2, 3, tzinfo=UTC),
        accepted_digest="d" * 64,
        profile_version="1.0.0",
        family_schema="system-design@1",
    )

    response = await QueryService(FakeReadModel((effect,))).facts({})

    assert {field["field"] for field in response["items"][0]["fields"]} == {
        "C12",
        "C18",
        "C23",
        "C24",
        "C25",
        "C26",
        "C27",
        "C33",
        "C34",
        "C35",
        "C36",
        "C37",
        "C38",
        "C51",
    }


@pytest.mark.asyncio
async def test_role_lineage_and_model_compatibility_follow_the_projection_matrix() -> None:
    lineage = QueryEffect(
        kind="role_lineage",
        key=("implementation@1", "role-local"),
        payload={
            "agentops.delivery.id": "must-not-leak",
            "agentops.family.schema": "implementation@1",
            "agentops.role.id": "role-local",
            "agentops.role.lineage.id": "role-family",
            "agentops.parent.role.id": "role-parent",
        },
        source_identity=("event", '["event","lineage-event-1"]'),
        recorded_at=datetime(2026, 8, 26, 1, 2, 3, tzinfo=UTC),
        accepted_digest="e" * 64,
        profile_version="1.0.0",
        family_schema="implementation@1",
    )
    model = QueryEffect(
        kind="model_attribution",
        key=("provider", "model-canonical", "role-local", "runtime-1", "1" * 32, "a" * 16),
        payload={"request_model": "model-requested"},
        source_identity=("span", f'["span","{"1" * 32}","{"a" * 16}"]'),
        recorded_at=datetime(2026, 8, 26, 1, 2, 3, tzinfo=UTC),
        accepted_digest="f" * 64,
        profile_version="1.0.0",
        family_schema="implementation@1",
    )

    response = await QueryService(FakeReadModel((lineage, model))).facts({})

    lineage_item, model_item = response["items"]
    assert {field["field"] for field in lineage_item["fields"]} == {"C30", "C31", "C32", "C49"}
    assert model_item["compatibility"]["dimensions"] == [
        {"field": "gen_ai.provider.name", "value": "provider"},
        {"field": "C57", "value": "model-canonical"},
        {"field": "C30", "value": "role-local"},
        {"field": "C06", "value": "runtime-1"},
    ]


@pytest.mark.asyncio
async def test_raw_debug_scrub_does_not_change_projected_relationship_fact() -> None:
    effect = QueryEffect(
        kind="finding_status",
        key=("finding-1", "scope-1", "review-1"),
        payload={
            "status": "OPEN",
            "writer_role_id": "writer",
            "writer_invocation_id": "writer-invocation",
            "reviewer_role_id": "reviewer",
            "reviewer_invocation_id": "reviewer-invocation",
        },
        source_identity=("event", '["event","finding-event-1"]'),
        recorded_at=datetime(2026, 8, 26, 1, 2, 3, tzinfo=UTC),
        accepted_digest="c" * 64,
        profile_version="1.0.0",
        family_schema="system-design@1",
    )

    response = await QueryService(FakeReadModel((effect,))).facts({})

    assert {field["field"] for field in response["items"][0]["fields"]} == {
        "C12",
        "C18",
        "C19",
        "C51",
        "C33",
        "C34",
        "C36",
        "C37",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("parameters", "route"),
    [
        ({"kind": "UNKNOWN"}, "facts"),
        ({"event_name": "unknown.event"}, "facts"),
        ({"kind": "FINDING_STATUS", "event_name": "review.summary"}, "facts"),
        ({"trace_id": "ABC"}, "facts"),
        ({"family_schema": "x" * 129}, "facts"),
        ({"delivery_id": "delivery%pattern"}, "facts"),
        ({"delivery_id": "delivery\\escape"}, "facts"),
        ({"delivery_id": "delivery\u0007control"}, "facts"),
        (
            {"recorded_from": "2026-08-27T00:00:00Z", "recorded_to": "2026-08-26T00:00:00Z"},
            "facts",
        ),
        ({"delivery_id": "d", "trace_id": "0" * 32}, "traces"),
    ],
)
async def test_invalid_or_incompatible_filters_fail_closed(
    parameters: dict[str, str], route: str
) -> None:
    service = QueryService(FakeReadModel(()))

    with pytest.raises(QueryError) as caught:
        await getattr(service, route)(parameters)

    assert caught.value.code is QueryErrorCode.INVALID_FILTER


@pytest.mark.asyncio
async def test_cursor_faults_are_bounded_and_repeated_parameters_are_bound() -> None:
    service = QueryService(FakeReadModel((), continuation_fault=SnapshotFault.EXPIRED))

    with pytest.raises(QueryError) as expired:
        await service.facts({"cursor": "a" * 43, "limit": "5"})
    assert expired.value.code is QueryErrorCode.CURSOR_EXPIRED

    first_model = FakeReadModel((review_summary(observed_count=0),))
    original_acquire = first_model.acquire_snapshot

    async def acquire_with_cursor(**kwargs: object) -> SnapshotPage[QueryEffect]:
        page = await original_acquire(**kwargs)  # type: ignore[arg-type]
        return SnapshotPage(
            contract_revision=page.contract_revision,
            read_model_revision=page.read_model_revision,
            snapshot_id=page.snapshot_id,
            resources=page.resources,
            next_cursor="b" * 43,
        )

    first_model.acquire_snapshot = acquire_with_cursor  # type: ignore[method-assign]
    bound = QueryService(first_model)
    await bound.facts({"limit": "5", "kind": "EVENT_CONTRIBUTION"})
    with pytest.raises(QueryError) as mismatch:
        await bound.facts({"cursor": "b" * 43, "limit": "6", "kind": "EVENT_CONTRIBUTION"})
    assert mismatch.value.code is QueryErrorCode.CURSOR_MISMATCH


@pytest.mark.asyncio
async def test_cursor_binding_normalizes_default_limit_order_and_utc_spelling() -> None:
    read_model = FakeReadModel((review_summary(observed_count=0),))
    original_acquire = read_model.acquire_snapshot

    async def acquire_with_cursor(**kwargs: object) -> SnapshotPage[QueryEffect]:
        page = await original_acquire(**kwargs)  # type: ignore[arg-type]
        return SnapshotPage(
            contract_revision=page.contract_revision,
            read_model_revision=page.read_model_revision,
            snapshot_id=page.snapshot_id,
            resources=page.resources,
            next_cursor="c" * 43,
        )

    read_model.acquire_snapshot = acquire_with_cursor  # type: ignore[method-assign]
    service = QueryService(read_model)
    await service.facts(
        {
            "recorded_from": "2026-08-26T01:02:03Z",
            "kind": "EVENT_CONTRIBUTION",
        }
    )

    response = await service.facts(
        {
            "kind": "EVENT_CONTRIBUTION",
            "limit": "100",
            "cursor": "c" * 43,
            "recorded_from": "2026-08-26T01:02:03.000000Z",
        }
    )

    assert response["contract"]["revision"] == "0.1.0"


@pytest.mark.asyncio
async def test_timestamp_filter_rejects_precision_that_cannot_be_normalized_exactly() -> None:
    service = QueryService(FakeReadModel((review_summary(observed_count=0),)))

    with pytest.raises(QueryError) as captured:
        await service.facts({"recorded_from": "2026-08-26T01:02:03.0000001Z"})

    assert captured.value.code is QueryErrorCode.INVALID_FILTER


@pytest.mark.asyncio
async def test_http_query_is_json_read_only_and_rejects_unknown_filters_and_bodies() -> None:
    service = QueryService(FakeReadModel((review_summary(observed_count=0),)))
    transport = ASGITransport(app=create_app(query_service=service))
    async with AsyncClient(transport=transport, base_url="http://evidence.test") as client:
        success = await client.get("/v1/evidence/facts")
        unknown = await client.get("/v1/evidence/facts?unknown=value")
        body = await client.request("GET", "/v1/evidence/facts", content=b"not-allowed")
        write = await client.post("/v1/evidence/facts", json={})
        missing = await client.get("/v1/evidence/unknown")
        excluded = await client.get("/v1/evidence/facts", headers={"accept": "text/plain"})
        excluded_q = await client.get(
            "/v1/evidence/facts", headers={"accept": "application/json;q=0"}
        )
        invalid_q = await client.get(
            "/v1/evidence/facts", headers={"accept": "application/json;q=1.001"}
        )
        shorthand_q = await client.get(
            "/v1/evidence/facts", headers={"accept": "application/json;q=.5"}
        )
        quoted_q = await client.get(
            "/v1/evidence/facts", headers={"accept": 'application/json;q="0.5"'}
        )
        repeated_q = await client.get(
            "/v1/evidence/facts", headers={"accept": "application/json;q=0;q=1"}
        )
        unlisted_method = await client.request("BREW", "/v1/evidence/facts")

    assert success.status_code == 200
    assert success.headers["content-type"].startswith("application/json")
    assert "www-authenticate" not in success.headers
    assert unknown.status_code == 400
    assert unknown.json()["error"]["code"] == "INVALID_FILTER"
    assert body.status_code == 400
    assert body.json()["error"]["code"] == "INVALID_FILTER"
    assert write.status_code == 405
    assert write.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "ROUTE_NOT_FOUND"
    assert excluded.status_code == 406
    assert excluded.json()["error"]["code"] == "NOT_ACCEPTABLE"
    assert excluded_q.status_code == 406
    assert invalid_q.status_code == 406
    assert shorthand_q.status_code == 406
    assert quoted_q.status_code == 406
    assert repeated_q.status_code == 406
    assert unlisted_method.status_code == 405
    assert unlisted_method.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
