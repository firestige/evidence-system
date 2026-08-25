"""Per-record admission transaction coordinator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from wsr_evidence.admission.validation import validate_record
from wsr_evidence.model import (
    Disposition,
    ProjectionConflict,
    ProjectionEffect,
    ProjectionPreconditionFailed,
    ValidatedRecord,
)
from wsr_evidence.projection.effects import project
from wsr_evidence.storage.transaction import TransactionManager


class AdmissionTransaction(Protocol):
    async def claim_identity(self, record: ValidatedRecord) -> Disposition: ...

    async def apply_effects(self, effects: tuple[ProjectionEffect, ...]) -> None: ...


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    disposition: Disposition
    effects: tuple[ProjectionEffect, ...] = ()
    reason: str | None = None


class AdmissionService:
    def __init__(self, storage: TransactionManager) -> None:
        self._storage = storage

    @staticmethod
    def project(record: ValidatedRecord) -> tuple[ProjectionEffect, ...]:
        return project(record)

    async def admit(self, logical: dict[str, Any]) -> AdmissionResult:
        record = validate_record(logical)
        try:
            async with self._storage.transaction() as transaction:
                disposition = await transaction.claim_identity(record)
                if disposition is not Disposition.ACCEPTED:
                    return AdmissionResult(disposition)
                effects = project(record)
                await transaction.apply_effects(effects)
                return AdmissionResult(disposition, effects)
        except ProjectionConflict as error:
            return AdmissionResult(Disposition.CONFLICT, reason=str(error))
        except ProjectionPreconditionFailed as error:
            return AdmissionResult(Disposition.REJECTED, reason=str(error))
