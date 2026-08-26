from datetime import UTC, datetime, timedelta

import pytest

from wsr_evidence.clock import FakeClock
from wsr_evidence.retention.config import RetentionSettings
from wsr_evidence.retention.postgresql import _projection_compatibility
from wsr_evidence.retention.service import RetentionService
from wsr_evidence.storage.read_model import (
    ExpiryBatch,
    ExpiryOwner,
    ExpiryResult,
    ResourceClass,
    RetentionPolicy,
)


class FakeMaintenance:
    def __init__(self) -> None:
        self.planned: list[tuple[ResourceClass, datetime, int, int]] = []
        self.applied: list[tuple[ExpiryBatch, datetime]] = []

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
                    owner_key=(resource_class.value,),
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


@pytest.mark.asyncio
async def test_fake_clock_plans_only_configured_physical_lifecycles() -> None:
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    maintenance = FakeMaintenance()
    service = RetentionService(
        maintenance,
        policy=RetentionPolicy(
            raw_debug_ttl=timedelta(0),
            trace_detail_ttl=None,
            factual_projection_ttl=timedelta(days=90),
            batch_size=17,
        ),
        clock=FakeClock(now),
    )

    results = await service.run_once()

    assert maintenance.planned == [
        (ResourceClass.RAW_DEBUG, now, 0, 17),
        (ResourceClass.FACTUAL_PROJECTION, now - timedelta(days=90), 7_776_000, 17),
    ]
    assert [batch.resource_class for batch, _ in maintenance.applied] == [
        ResourceClass.RAW_DEBUG,
        ResourceClass.FACTUAL_PROJECTION,
    ]
    assert all(applied_at == now for _, applied_at in maintenance.applied)
    assert len(results) == 2


def test_environment_projects_exact_retention_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WSR_EVIDENCE_RAW_DEBUG_TTL", "P1D")
    monkeypatch.setenv("WSR_EVIDENCE_TRACE_DETAIL_TTL", "NEVER")
    monkeypatch.setenv("WSR_EVIDENCE_FACTUAL_PROJECTION_TTL", "P90D")
    monkeypatch.setenv("WSR_EVIDENCE_RETENTION_BATCH_SIZE", "17")
    monkeypatch.setenv("WSR_EVIDENCE_RETENTION_INTERVAL_SECONDS", "30")

    settings = RetentionSettings.from_environment()

    assert settings.policy == RetentionPolicy(
        raw_debug_ttl=timedelta(days=1),
        trace_detail_ttl=None,
        factual_projection_ttl=timedelta(days=90),
        batch_size=17,
        interval=timedelta(seconds=30),
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("WSR_EVIDENCE_RAW_DEBUG_TTL", "NEVER"),
        ("WSR_EVIDENCE_TRACE_DETAIL_TTL", "PT24H"),
        ("WSR_EVIDENCE_FACTUAL_PROJECTION_TTL", "P1M"),
        ("WSR_EVIDENCE_RETENTION_BATCH_SIZE", "1.5"),
        ("WSR_EVIDENCE_RETENTION_INTERVAL_SECONDS", "ten"),
        ("WSR_EVIDENCE_ACCEPTED_PROVENANCE_TTL", "P1D"),
    ],
)
def test_environment_rejects_unsupported_retention_values_before_runtime_effects(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError):
        RetentionSettings.from_environment()


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
