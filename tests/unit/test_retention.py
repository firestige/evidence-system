from datetime import UTC, datetime, timedelta

import pytest

from wsr_evidence.clock import FakeClock
from wsr_evidence.retention.config import RetentionSettings
from wsr_evidence.retention.postgresql import _projection_compatibility
from wsr_evidence.retention.scheduler import run_retention_loop
from wsr_evidence.retention.service import RetentionService
from wsr_evidence.storage.read_model import (
    DeliveryDeletionBatch,
    DeliveryDeletionResult,
    DeliveryRetentionPolicy,
    ExpiryBatch,
    ExpiryOwner,
    ExpiryResult,
    ResourceClass,
)


class FakeMaintenance:
    def __init__(self) -> None:
        self.planned: list[tuple[ResourceClass, datetime, int, int]] = []
        self.applied: list[tuple[ExpiryBatch, datetime]] = []
        self.delivery_planned: list[tuple[datetime, int, int]] = []
        self.delivery_applied: list[tuple[DeliveryDeletionBatch, datetime]] = []

    async def plan_expiry(
        self,
        *,
        resource_class: ResourceClass,
        policy_revision: str,
        cutoff: datetime,
        ttl_seconds: int,
        limit: int,
    ) -> ExpiryBatch:
        self.planned.append((resource_class, cutoff, ttl_seconds, limit))
        return ExpiryBatch.create(
            resource_class=resource_class,
            policy_revision=policy_revision,
            cutoff=cutoff,
            ttl_seconds=ttl_seconds,
            members=(
                ExpiryOwner(
                    resource_kind={
                        ResourceClass.RAW_DEBUG: "RAW_DEBUG",
                        ResourceClass.TRACE_DETAIL: "NODE",
                        ResourceClass.FACTUAL_PROJECTION: "EVENT_CONTRIBUTION",
                    }[resource_class],
                    owner_key={
                        ResourceClass.RAW_DEBUG: ("event", "event-1"),
                        ResourceClass.TRACE_DETAIL: ("1" * 32, "a" * 16),
                        ResourceClass.FACTUAL_PROJECTION: ("review.summary", "event-1"),
                    }[resource_class],
                ),
            ),
        )

    async def apply_expiry(self, *, batch: ExpiryBatch, clock_now: datetime) -> ExpiryResult:
        self.applied.append((batch, clock_now))
        return ExpiryResult(
            batch_identity=batch.batch_identity,
            selected=1,
            expired=1,
            already_expired=0,
        )

    async def plan_delivery_deletion(
        self,
        *,
        policy_revision: str,
        cutoff: datetime,
        ttl_seconds: int,
        limit: int,
    ) -> DeliveryDeletionBatch:
        self.delivery_planned.append((cutoff, ttl_seconds, limit))
        return DeliveryDeletionBatch.create(
            policy_revision=policy_revision,
            cutoff=cutoff,
            ttl_seconds=ttl_seconds,
            delivery_ids=("delivery-2", "delivery-1"),
        )

    async def apply_delivery_deletion(
        self, *, batch: DeliveryDeletionBatch, clock_now: datetime
    ) -> DeliveryDeletionResult:
        self.delivery_applied.append((batch, clock_now))
        return DeliveryDeletionResult(
            batch_identity=batch.batch_identity,
            selected=2,
            deleted=2,
            already_deleted=0,
        )


class FakeRetentionRunner:
    def __init__(self) -> None:
        self.calls = 0
        self.ran = __import__("asyncio").Event()

    async def run_once(self) -> tuple[ExpiryResult, ...]:
        self.calls += 1
        self.ran.set()
        return ()


@pytest.mark.asyncio
async def test_retention_scheduler_runs_immediately_and_stops_by_cancellation() -> None:
    import asyncio

    runner = FakeRetentionRunner()
    task = asyncio.create_task(run_retention_loop(runner, interval=timedelta(seconds=10)))
    await asyncio.wait_for(runner.ran.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert runner.calls == 1


@pytest.mark.asyncio
async def test_fake_clock_plans_raw_scrub_and_one_delivery_deletion_lifecycle() -> None:
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    maintenance = FakeMaintenance()
    service = RetentionService(
        maintenance,
        policy=DeliveryRetentionPolicy(
            raw_debug_ttl=timedelta(0),
            delivery_ttl=timedelta(days=90),
            batch_size=17,
        ),
        clock=FakeClock(now),
    )

    results = await service.run_once()

    assert maintenance.planned == [
        (ResourceClass.RAW_DEBUG, now, 0, 17),
    ]
    assert [batch.resource_class for batch, _ in maintenance.applied] == [ResourceClass.RAW_DEBUG]
    assert maintenance.delivery_planned == [(now - timedelta(days=90), 7_776_000, 17)]
    assert maintenance.delivery_applied[0][0].delivery_ids == ("delivery-1", "delivery-2")
    assert all(applied_at == now for _, applied_at in maintenance.applied)
    assert all(applied_at == now for _, applied_at in maintenance.delivery_applied)
    assert len(results) == 2


def test_environment_projects_exact_retention_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WSR_EVIDENCE_RAW_DEBUG_TTL", "P1D")
    monkeypatch.setenv("WSR_EVIDENCE_DELIVERY_TTL", "P90D")
    monkeypatch.setenv("WSR_EVIDENCE_RETENTION_BATCH_SIZE", "17")
    monkeypatch.setenv("WSR_EVIDENCE_RETENTION_INTERVAL_SECONDS", "30")

    settings = RetentionSettings.from_environment()

    assert settings.policy == DeliveryRetentionPolicy(
        raw_debug_ttl=timedelta(days=1),
        delivery_ttl=timedelta(days=90),
        batch_size=17,
        interval=timedelta(seconds=30),
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("WSR_EVIDENCE_RAW_DEBUG_TTL", "NEVER"),
        ("WSR_EVIDENCE_DELIVERY_TTL", "PT24H"),
        ("WSR_EVIDENCE_TRACE_DETAIL_TTL", "P30D"),
        ("WSR_EVIDENCE_FACTUAL_PROJECTION_TTL", "P365D"),
        ("WSR_EVIDENCE_ACCEPTED_PROVENANCE_TTL", "P1D"),
        ("WSR_EVIDENCE_RETENTION_BATCH_SIZE", "1.5"),
        ("WSR_EVIDENCE_RETENTION_INTERVAL_SECONDS", "ten"),
    ],
)
def test_environment_rejects_unsupported_retention_values_before_runtime_effects(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError):
        RetentionSettings.from_environment()


def test_delivery_retention_policy_defaults_and_bounds() -> None:
    assert DeliveryRetentionPolicy().delivery_ttl == timedelta(days=30)
    assert DeliveryRetentionPolicy(delivery_ttl=None).delivery_ttl is None
    with pytest.raises(ValueError):
        DeliveryRetentionPolicy(delivery_ttl=timedelta(0))
    with pytest.raises(ValueError):
        DeliveryRetentionPolicy(delivery_ttl=timedelta(days=3651))


def test_delivery_deletion_batch_is_unique_sorted_and_digest_stable() -> None:
    cutoff = datetime(2026, 8, 26, 12, tzinfo=UTC)
    first = DeliveryDeletionBatch.create(
        policy_revision="2.0.0",
        cutoff=cutoff,
        ttl_seconds=30 * 86_400,
        delivery_ids=("delivery-b", "delivery-a"),
    )
    second = DeliveryDeletionBatch.create(
        policy_revision="2.0.0",
        cutoff=cutoff,
        ttl_seconds=30 * 86_400,
        delivery_ids=("delivery-a", "delivery-b"),
    )
    assert first == second
    assert first.delivery_ids == ("delivery-a", "delivery-b")
    with pytest.raises(ValueError):
        DeliveryDeletionBatch.create(
            policy_revision="2.0.0",
            cutoff=cutoff,
            ttl_seconds=30 * 86_400,
            delivery_ids=("delivery-a", "delivery-a"),
        )


def test_expiry_tombstone_preserves_exact_model_compatibility() -> None:
    compatibility = _projection_compatibility(
        effect_kind="model_attribution",
        effect_key=("provider", "model", "role", "runtime", "1" * 32, "a" * 16),
        payload={"request_model": "requested"},
        family="implementation@1",
    )

    assert compatibility == [
        ["family_schema", "implementation@1"],
        ["event_name", None],
        ["completeness", None],
        ["gen_ai.provider.name", "provider"],
        ["C57", "model"],
        ["C30", "role"],
        ["C06", "runtime"],
    ]
