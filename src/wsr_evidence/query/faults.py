"""Bounded faults emitted by the private PostgreSQL query adapter."""

from enum import StrEnum


class SnapshotFault(StrEnum):
    INVALID = "INVALID"
    MISMATCH = "MISMATCH"
    EXPIRED = "EXPIRED"
    BOUND_EXCEEDED = "BOUND_EXCEEDED"
    UNAVAILABLE = "UNAVAILABLE"


class SnapshotError(Exception):
    def __init__(self, fault: SnapshotFault, message: str) -> None:
        super().__init__(message)
        self.fault = fault
