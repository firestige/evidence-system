"""Process configuration with a fail-closed network boundary."""

from __future__ import annotations

import os
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Literal, cast


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    host: str = "127.0.0.1"
    port: int = 4318
    bind_scope: Literal["loopback", "container"] = "loopback"
    database_url: str | None = None

    def __post_init__(self) -> None:
        if self.bind_scope not in {"loopback", "container"}:
            raise ValueError("Evidence bind scope must be loopback or container")
        try:
            address = ip_address(self.host)
        except ValueError as error:
            raise ValueError("Evidence host must be an IP loopback address") from error
        if self.bind_scope == "container" and self.host not in {"0.0.0.0", "::"}:
            raise ValueError("Container scope must bind an IP wildcard address")
        if self.bind_scope == "loopback" and not address.is_loopback:
            raise ValueError("Evidence host must be an IP loopback address")
        if not 1 <= self.port <= 65535:
            raise ValueError("Evidence port must be between 1 and 65535")

    @classmethod
    def from_environment(cls) -> RuntimeSettings:
        bind_scope = os.environ.get("WSR_EVIDENCE_BIND_SCOPE", "loopback")
        if bind_scope not in {"loopback", "container"}:
            raise ValueError("Evidence bind scope must be loopback or container")
        return cls(
            host=os.environ.get("WSR_EVIDENCE_HOST", "127.0.0.1"),
            port=int(os.environ.get("WSR_EVIDENCE_PORT", "4318")),
            bind_scope=cast(Literal["loopback", "container"], bind_scope),
            database_url=os.environ.get("WSR_EVIDENCE_DATABASE_URL"),
        )
