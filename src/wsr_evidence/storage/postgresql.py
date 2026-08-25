"""Psycopg implementation of the per-record transaction seam."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from wsr_evidence.model import (
    Disposition,
    ProjectionConflict,
    ProjectionEffect,
    ProjectionPreconditionFailed,
    ValidatedRecord,
)


def _key_json(key: tuple[Any, ...]) -> str:
    return json.dumps(key, ensure_ascii=False, separators=(",", ":"))


class PostgresTransaction:
    def __init__(self, connection: AsyncConnection[Any]) -> None:
        self._connection = connection

    async def claim_identity(self, record: ValidatedRecord) -> Disposition:
        identity_key = _key_json(record.identity)
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO accepted_records
                    (identity_kind, identity_key, canonical_digest, profile_version,
                     family_schema, logical_record)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (identity_kind, identity_key) DO NOTHING
                RETURNING canonical_digest
                """,
                (
                    record.identity[0],
                    identity_key,
                    record.digest,
                    "1.0.0",
                    record.attributes.get("agentops.family.schema"),
                    json.dumps(record.logical, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            inserted = await cursor.fetchone()
            if inserted is not None:
                return Disposition.ACCEPTED
            await cursor.execute(
                """
                SELECT canonical_digest FROM accepted_records
                WHERE identity_kind = %s AND identity_key = %s
                FOR UPDATE
                """,
                (record.identity[0], identity_key),
            )
            existing = await cursor.fetchone()
        if existing is None:
            raise RuntimeError("accepted identity disappeared during transaction")
        return Disposition.DUPLICATE if existing[0] == record.digest else Disposition.CONFLICT

    async def apply_effects(self, effects: tuple[ProjectionEffect, ...]) -> None:
        for effect in effects:
            if effect.kind.startswith("require_"):
                await self._require_effect(effect)
            else:
                await self._first_write(effect)

    async def _require_effect(self, effect: ProjectionEffect) -> None:
        required_kind = effect.kind.removeprefix("require_")
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                "SELECT 1 FROM projection_effects WHERE effect_kind = %s AND effect_key = %s",
                (required_kind, _key_json(effect.key)),
            )
            found = await cursor.fetchone()
        if found is None:
            raise ProjectionPreconditionFailed(
                f"{effect.kind} selected an unaccepted projection identity"
            )

    async def _first_write(self, effect: ProjectionEffect) -> None:
        key = _key_json(effect.key)
        payload = json.dumps(
            effect.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO projection_effects (effect_kind, effect_key, payload)
                VALUES (%s, %s, %s::jsonb)
                ON CONFLICT (effect_kind, effect_key) DO NOTHING
                RETURNING payload
                """,
                (effect.kind, key, payload),
            )
            inserted = await cursor.fetchone()
            if inserted is not None:
                return
            await cursor.execute(
                """
                SELECT payload FROM projection_effects
                WHERE effect_kind = %s AND effect_key = %s
                FOR UPDATE
                """,
                (effect.kind, key),
            )
            existing = await cursor.fetchone()
        if existing is None:
            raise RuntimeError("projection identity disappeared during transaction")
        existing_payload = existing[0]
        if existing_payload != effect.payload:
            raise ProjectionConflict(f"{effect.kind} first-write conflict")


class PostgresStorage:
    def __init__(self, pool: AsyncConnectionPool[Any]) -> None:
        self._pool = pool

    @classmethod
    async def open(cls, database_url: str) -> PostgresStorage:
        pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
            conninfo=database_url,
            min_size=1,
            max_size=10,
            open=False,
        )
        await pool.open()
        await pool.wait()
        return cls(pool)

    async def close(self) -> None:
        await self._pool.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[PostgresTransaction]:
        async with self._pool.connection() as connection, connection.transaction():
            yield PostgresTransaction(connection)
