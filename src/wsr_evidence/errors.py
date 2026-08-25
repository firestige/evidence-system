"""Stable internal error categories; transport mappings live at the boundary."""

from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_OBSERVATION = "invalid_observation"
    IDENTITY_CONFLICT = "identity_conflict"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    INTERNAL = "internal"


class EvidenceError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
