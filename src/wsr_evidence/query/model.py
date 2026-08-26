"""Wave 7 private rows returned by the query adapter."""

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from wsr_evidence.storage.read_model import StoredEffect


@dataclass(frozen=True, slots=True)
class QueryEffect(StoredEffect):
    accepted_digest: str
    profile_version: str
    family_schema: str | None
    logical_record: dict[str, Any]


@runtime_checkable
class SnapshotReleaser(Protocol):
    async def release_snapshot(self, snapshot_id: str) -> None: ...
