"""Policy-driven orchestration over the shared expiry maintenance port."""

from __future__ import annotations

from wsr_evidence.clock import Clock, SystemClock
from wsr_evidence.storage.read_model import (
    DeliveryDeletionResult,
    DeliveryRetentionMaintenance,
    DeliveryRetentionPolicy,
    ExpiryResult,
    ResourceClass,
)


class RetentionService:
    def __init__(
        self,
        maintenance: DeliveryRetentionMaintenance,
        *,
        policy: DeliveryRetentionPolicy | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._maintenance = maintenance
        self._policy = policy or DeliveryRetentionPolicy()
        self._clock = clock or SystemClock()

    async def run_once(self) -> tuple[ExpiryResult | DeliveryDeletionResult, ...]:
        now = self._clock.now()
        raw_batch = await self._maintenance.plan_expiry(
            resource_class=ResourceClass.RAW_DEBUG,
            policy_revision=self._policy.revision,
            cutoff=now - self._policy.raw_debug_ttl,
            ttl_seconds=int(self._policy.raw_debug_ttl.total_seconds()),
            limit=self._policy.batch_size,
        )
        results: list[ExpiryResult | DeliveryDeletionResult] = [
            await self._maintenance.apply_expiry(batch=raw_batch, clock_now=now)
        ]
        if self._policy.delivery_ttl is not None:
            delivery_batch = await self._maintenance.plan_delivery_deletion(
                policy_revision=self._policy.revision,
                cutoff=now - self._policy.delivery_ttl,
                ttl_seconds=int(self._policy.delivery_ttl.total_seconds()),
                limit=self._policy.batch_size,
            )
            results.append(
                await self._maintenance.apply_delivery_deletion(batch=delivery_batch, clock_now=now)
            )
        return tuple(results)
