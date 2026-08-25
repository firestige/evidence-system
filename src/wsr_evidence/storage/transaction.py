"""Database seam. Admission owns when one transaction begins and ends."""

from contextlib import AbstractAsyncContextManager
from typing import Protocol


class EvidenceTransaction(Protocol):
    """Marker protocol extended by repositories in their owning waves."""


class TransactionManager(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[EvidenceTransaction]: ...
