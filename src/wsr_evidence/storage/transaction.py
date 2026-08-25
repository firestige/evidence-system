"""Database seam. Admission owns when one transaction begins and ends."""

from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol


class EvidenceTransaction(Protocol):
    async def claim_identity(self, record: Any) -> Any: ...

    async def apply_effects(self, effects: tuple[Any, ...]) -> None: ...


class TransactionManager(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[EvidenceTransaction]: ...
