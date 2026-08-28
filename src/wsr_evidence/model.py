"""Shared immutable values crossing component boundaries."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal


class Disposition(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ValidatedRecord:
    logical: dict[str, Any]
    profile_version: str
    identity: tuple[str, ...]
    digest: str
    attributes: dict[str, str | int | float]
    record_type: str
    event_name: str | None


@dataclass(frozen=True, slots=True)
class ProjectionEffect:
    kind: str
    key: tuple[Any, ...]
    payload: dict[str, Any]
    operation: Literal["first_write"] = "first_write"


class ProjectionError(Exception):
    """A complete record cannot be projected without violating first-write truth."""


class ProjectionConflict(ProjectionError):
    pass


class ProjectionPreconditionFailed(ProjectionError):
    pass
