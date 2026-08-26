from datetime import UTC, datetime, timedelta

import pytest

from wsr_evidence.storage.read_model import (
    DEFAULT_FACTUAL_PROJECTION_TTL,
    DEFAULT_PAGE_LIMIT,
    DEFAULT_RAW_DEBUG_TTL,
    DEFAULT_RETENTION_BATCH_SIZE,
    DEFAULT_RETENTION_INTERVAL,
    DEFAULT_SNAPSHOT_LEASE_LIMIT,
    DEFAULT_SNAPSHOT_LEASE_TTL,
    DEFAULT_TRACE_DETAIL_TTL,
    MAX_PAGE_LIMIT,
    QUERY_CONTRACT_REVISION,
    RETENTION_POLICY_REVISION,
    Availability,
    Completeness,
    ExpiryBatch,
    ExpiryOwner,
    ExpiryRecord,
    ExpiryResult,
    ExpiryState,
    QueryExpiryReadModel,
    ResourceClass,
    RetentionPolicy,
    SnapshotPage,
    TraceDetailState,
    TraceSummary,
    TruthState,
)


def test_wave6_versions_and_defaults_are_exact() -> None:
    assert QUERY_CONTRACT_REVISION == "0.1.0"
    assert RETENTION_POLICY_REVISION == "1.0.0"
    assert DEFAULT_PAGE_LIMIT == 100
    assert MAX_PAGE_LIMIT == 200
    assert timedelta(seconds=60) == DEFAULT_SNAPSHOT_LEASE_TTL
    assert DEFAULT_SNAPSHOT_LEASE_LIMIT == 4
    assert timedelta(0) == DEFAULT_RAW_DEBUG_TTL
    assert timedelta(days=30) == DEFAULT_TRACE_DETAIL_TTL
    assert timedelta(days=365) == DEFAULT_FACTUAL_PROJECTION_TTL
    assert DEFAULT_RETENTION_BATCH_SIZE == 500
    assert timedelta(seconds=60) == DEFAULT_RETENTION_INTERVAL


def test_truth_state_preserves_completeness_and_distinguishes_expiry() -> None:
    final_zero = TruthState(
        completeness=Completeness.FINAL,
        availability=Availability.AVAILABLE,
        expiry=ExpiryState.ACTIVE,
        expires_at=datetime(2026, 9, 25, tzinfo=UTC),
    )
    expired_final = TruthState(
        completeness=Completeness.FINAL,
        availability=Availability.UNAVAILABLE,
        expiry=ExpiryState.EXPIRED,
        expires_at=datetime(2026, 9, 25, tzinfo=UTC),
    )

    assert final_zero.completeness is Completeness.FINAL
    assert expired_final.completeness is Completeness.FINAL
    assert expired_final.expiry is ExpiryState.EXPIRED


@pytest.mark.parametrize(
    ("completeness", "availability", "expiry"),
    [
        (Completeness.FINAL, Availability.UNAVAILABLE, ExpiryState.ACTIVE),
        (Completeness.LOWER_BOUND, Availability.UNAVAILABLE, ExpiryState.ACTIVE),
        (Completeness.NOT_APPLICABLE, Availability.UNAVAILABLE, ExpiryState.ACTIVE),
        (Completeness.UNAVAILABLE, Availability.AVAILABLE, ExpiryState.ACTIVE),
        (None, Availability.AVAILABLE, ExpiryState.EXPIRED),
    ],
)
def test_truth_state_rejects_combinations_that_rewrite_truth(
    completeness: Completeness | None,
    availability: Availability,
    expiry: ExpiryState,
) -> None:
    with pytest.raises(ValueError):
        TruthState(
            completeness=completeness,
            availability=availability,
            expiry=expiry,
            expires_at=None,
        )


def test_retention_policy_defaults_keep_accepted_provenance_forever() -> None:
    policy = RetentionPolicy()

    assert policy.revision == "1.0.0"
    assert policy.raw_debug_ttl == timedelta(0)
    assert policy.accepted_provenance_ttl is None
    assert policy.trace_detail_ttl == timedelta(days=30)
    assert policy.factual_projection_ttl == timedelta(days=365)
    assert policy.batch_size == 500
    assert policy.interval == timedelta(seconds=60)


@pytest.mark.parametrize(
    "overrides",
    [
        {"raw_debug_ttl": timedelta(days=1, seconds=1)},
        {"trace_detail_ttl": timedelta(hours=23)},
        {"trace_detail_ttl": timedelta(days=366)},
        {"factual_projection_ttl": timedelta(days=29)},
        {"factual_projection_ttl": timedelta(days=3651)},
        {"batch_size": 0},
        {"batch_size": 1001},
        {"interval": timedelta(seconds=9)},
        {"interval": timedelta(seconds=3601)},
    ],
)
def test_retention_policy_rejects_out_of_range_values(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RetentionPolicy(**overrides)  # type: ignore[arg-type]


def test_expiry_batch_identity_is_canonical_and_rejects_duplicates() -> None:
    cutoff = datetime(2026, 8, 26, tzinfo=UTC)
    trace_a = ExpiryOwner(resource_kind="NODE", owner_key=("1" * 32, "a" * 16))
    trace_b = ExpiryOwner(resource_kind="LINK", owner_key=("1" * 32, "a" * 16, "2" * 32, "b" * 16))
    first = ExpiryBatch.create(
        resource_class=ResourceClass.TRACE_DETAIL,
        policy_revision="1.0.0",
        cutoff=cutoff,
        ttl_seconds=2_592_000,
        members=(trace_b, trace_a),
    )
    second = ExpiryBatch.create(
        resource_class=ResourceClass.TRACE_DETAIL,
        policy_revision="1.0.0",
        cutoff=cutoff,
        ttl_seconds=2_592_000,
        members=(trace_a, trace_b),
    )

    assert first == second
    assert first.members == (trace_b, trace_a)
    assert len(first.batch_identity) == 64

    with pytest.raises(ValueError):
        ExpiryBatch.create(
            resource_class=ResourceClass.TRACE_DETAIL,
            policy_revision="1.0.0",
            cutoff=cutoff,
            ttl_seconds=2_592_000,
            members=(trace_a, trace_a),
        )


@pytest.mark.parametrize(
    "owner",
    [
        ExpiryOwner(resource_kind="RAW_DEBUG", owner_key=("unknown", "id")),
        ExpiryOwner(resource_kind="NODE", owner_key=("1" * 32,)),
        ExpiryOwner(resource_kind="PARENT_EDGE", owner_key=("1" * 32, "a" * 16)),
        ExpiryOwner(resource_kind="LINK", owner_key=("1" * 32, "a" * 16, "2" * 32)),
        ExpiryOwner(resource_kind="DELIVERY_ROOT_BINDING", owner_key=("not-a-trace",)),
    ],
)
def test_expiry_batch_rejects_owner_keys_outside_the_closed_kind_shape(
    owner: ExpiryOwner,
) -> None:
    with pytest.raises(ValueError):
        ExpiryBatch.create(
            resource_class=(
                ResourceClass.RAW_DEBUG
                if owner.resource_kind == "RAW_DEBUG"
                else ResourceClass.TRACE_DETAIL
                if owner.resource_kind in {"NODE", "PARENT_EDGE", "LINK"}
                else ResourceClass.FACTUAL_PROJECTION
            ),
            policy_revision="1.0.0",
            cutoff=datetime(2026, 8, 26, tzinfo=UTC),
            ttl_seconds=0,
            members=(owner,),
        )


def test_expiry_record_rejects_noncanonical_compatibility_pairs() -> None:
    common = {
        "resource_class": ResourceClass.FACTUAL_PROJECTION,
        "owner_key": ("review.summary", "event-1"),
        "source_identity": ("event", '["event","event-1"]'),
        "resource_kind": "EVENT_CONTRIBUTION",
        "recorded_at": datetime(2026, 8, 26, tzinfo=UTC),
        "policy_revision": "1.0.0",
        "expires_at": datetime(2027, 8, 26, tzinfo=UTC),
        "expired_at": datetime(2027, 8, 27, tzinfo=UTC),
    }

    with pytest.raises(ValueError):
        ExpiryRecord(
            **common,
            compatibility=(("event_name", "review.summary"), ("family_schema", None)),
        )
    with pytest.raises(ValueError):
        ExpiryRecord(
            **common,
            compatibility=(
                ("family_schema", None),
                ("event_name", "review.summary"),
                ("completeness", "FINAL"),
                ("unknown", "value"),
            ),
        )


def test_equal_owner_keys_in_different_resource_kinds_remain_distinct() -> None:
    batch = ExpiryBatch.create(
        resource_class=ResourceClass.FACTUAL_PROJECTION,
        policy_revision="1.0.0",
        cutoff=datetime(2026, 8, 26, tzinfo=UTC),
        ttl_seconds=31_536_000,
        members=(
            ExpiryOwner(resource_kind="FINDING_ASSERTION", owner_key=("same", "key")),
            ExpiryOwner(resource_kind="ROLE_LINEAGE", owner_key=("same", "key")),
        ),
    )

    assert len(batch.members) == 2


def test_accepted_provenance_cannot_be_planned_for_expiry() -> None:
    with pytest.raises(ValueError):
        ExpiryBatch.create(
            resource_class=ResourceClass.ACCEPTED_PROVENANCE,
            policy_revision="1.0.0",
            cutoff=datetime(2026, 8, 26, tzinfo=UTC),
            ttl_seconds=0,
            members=(),
        )


def test_expiry_batch_has_a_cross_language_canonical_digest_vector() -> None:
    batch = ExpiryBatch.create(
        resource_class=ResourceClass.FACTUAL_PROJECTION,
        policy_revision="1.0.0",
        cutoff=datetime(2026, 8, 26, 1, 2, 3, tzinfo=UTC),
        ttl_seconds=31_536_000,
        members=(
            ExpiryOwner(resource_kind="ROLE_LINEAGE", owner_key=("家", "角色")),
            ExpiryOwner(resource_kind="FINDING_ASSERTION", owner_key=("same", "key")),
        ),
    )

    assert batch.ttl_seconds == 31_536_000
    assert (
        batch.batch_identity == "cbae1a7afcfa501cc6ee709821b6e6ecf354fe8b540d9a8109ab4c6e4f32a4ad"
    )


def test_trace_summaries_are_closed_and_allow_partial_detail() -> None:
    available = TraceSummary(trace_id="a" * 32, state=TraceDetailState.AVAILABLE)
    partial = TraceSummary(trace_id="b" * 32, state=TraceDetailState.PARTIAL)
    expired = TraceSummary(trace_id="c" * 32, state=TraceDetailState.EXPIRED)

    assert [summary.state.value for summary in (available, partial, expired)] == [
        "AVAILABLE",
        "PARTIAL",
        "EXPIRED",
    ]

    with pytest.raises(ValueError, match="trace_id"):
        TraceSummary(trace_id="not-a-trace", state=TraceDetailState.AVAILABLE)


def test_expiry_result_requires_an_exact_partition() -> None:
    result = ExpiryResult(
        batch_identity="a" * 64,
        selected=3,
        expired=2,
        already_expired=1,
    )
    assert result.expired + result.already_expired == result.selected

    with pytest.raises(ValueError):
        ExpiryResult(
            batch_identity="a" * 64,
            selected=3,
            expired=1,
            already_expired=1,
        )


def test_snapshot_page_and_read_model_protocol_are_runtime_checkable() -> None:
    page: SnapshotPage[str] = SnapshotPage(
        contract_revision="0.1.0",
        read_model_revision="1.0.0",
        snapshot_id="snapshot-1",
        resources=("resource-1",),
        next_cursor=None,
    )

    assert page.resources == ("resource-1",)
    assert QueryExpiryReadModel is not None
