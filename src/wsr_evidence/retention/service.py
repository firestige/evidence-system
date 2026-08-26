"""Policy-driven orchestration over the shared expiry maintenance port."""

from __future__ import annotations

from wsr_evidence.clock import Clock, SystemClock
from wsr_evidence.storage.read_model import (
    ExpiryMaintenance,
    ExpiryResult,
    ResourceClass,
    RetentionPolicy,
)


class RetentionService:
    def __init__(
        self,
        maintenance: ExpiryMaintenance,
        *,
        policy: RetentionPolicy | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._maintenance = maintenance
        self._policy = policy or RetentionPolicy()
        self._clock = clock or SystemClock()

    async def run_once(self) -> tuple[ExpiryResult, ...]:
        now = self._clock.now()
        lifecycles = (
            (ResourceClass.RAW_DEBUG, self._policy.raw_debug_ttl),
            (ResourceClass.TRACE_DETAIL, self._policy.trace_detail_ttl),
            (ResourceClass.FACTUAL_PROJECTION, self._policy.factual_projection_ttl),
        )
        results = []
        for resource_class, ttl in lifecycles:
            if ttl is None:
                continue
            batch = await self._maintenance.plan_expiry(
                resource_class=resource_class,
                policy_revision=self._policy.revision,
                cutoff=now - ttl,
                limit=self._policy.batch_size,
            )
            results.append(await self._maintenance.apply_expiry(batch=batch, clock_now=now))
        return tuple(results)
