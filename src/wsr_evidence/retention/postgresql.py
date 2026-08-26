"""PostgreSQL implementation of independent retention maintenance."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, cast

from psycopg_pool import AsyncConnectionPool

from wsr_evidence.storage.postgresql import PostgresStorage
from wsr_evidence.storage.read_model import (
    ExpiryBatch,
    ExpiryOwner,
    ExpiryResult,
    OwnerKey,
    ResourceClass,
)

TRACE_KINDS = ("trace_node", "trace_parent_edge", "trace_link")
FACTUAL_KINDS = (
    "factual_contribution",
    "finding_assertion",
    "finding_target",
    "finding_status",
    "finding_fix",
    "finding_recheck",
    "role_lineage",
    "delivery_root_binding",
    "model_attribution",
)
PUBLIC_KINDS = {
    "trace_node": "NODE",
    "trace_parent_edge": "PARENT_EDGE",
    "trace_link": "LINK",
    "factual_contribution": "EVENT_CONTRIBUTION",
    "finding_assertion": "FINDING_ASSERTION",
    "finding_target": "FINDING_TARGET",
    "finding_status": "FINDING_STATUS",
    "finding_fix": "FINDING_FIX",
    "finding_recheck": "FINDING_RECHECK",
    "role_lineage": "ROLE_LINEAGE",
    "delivery_root_binding": "DELIVERY_ROOT_BINDING",
    "model_attribution": "MODEL_ATTRIBUTION",
}
INTERNAL_KINDS = {public: internal for internal, public in PUBLIC_KINDS.items()}
PUBLIC_KIND_SQL = (
    "CASE pe.effect_kind "
    + " ".join(f"WHEN '{internal}' THEN '{public}'" for internal, public in PUBLIC_KINDS.items())
    + " END"
)

COMPATIBILITY_DIMENSIONS = {
    "usage": (
        ("C42", "agentops.usage.kind"),
        ("C43", "agentops.usage.unit"),
        ("C44", "agentops.usage.source"),
        ("C45", "agentops.usage.source.id"),
    ),
    "implementation.summary": (
        ("I05", "agentops.coverage.dimension"),
        ("I08", "agentops.coverage.scope"),
        ("I09", "agentops.coverage.tool.id"),
        ("I10", "agentops.coverage.format"),
    ),
    "test.summary": (("C28", "agentops.artifact.id"), ("C29", "agentops.artifact.digest")),
    "review.summary": (("C13", "agentops.review.lens"), ("C14", "agentops.review.scope")),
}


def _key_json(key: OwnerKey) -> str:
    return json.dumps(key, ensure_ascii=False, separators=(",", ":"))


class PostgresRetentionMaintenance:
    def __init__(self, pool: AsyncConnectionPool[Any]) -> None:
        self._pool = pool

    @classmethod
    def from_storage(cls, storage: PostgresStorage) -> PostgresRetentionMaintenance:
        return cls(storage._pool)  # noqa: SLF001 -- shared runtime pool is the approved seam.

    async def plan_expiry(
        self,
        *,
        resource_class: ResourceClass,
        policy_revision: str,
        cutoff: datetime,
        limit: int,
    ) -> ExpiryBatch:
        if resource_class is ResourceClass.ACCEPTED_PROVENANCE:
            raise ValueError("accepted provenance cannot expire")
        if not 1 <= limit <= 1000:
            raise ValueError("retention plan limit must be in [1,1000]")
        async with self._pool.connection() as connection, connection.cursor() as cursor:
            if resource_class is ResourceClass.RAW_DEBUG:
                await cursor.execute(
                    """
                    SELECT 'RAW_DEBUG', ar.identity_key
                    FROM accepted_records ar
                    LEFT JOIN retention_expiry_markers marker
                      ON marker.resource_class = 'RAW_DEBUG'
                     AND marker.resource_kind = 'RAW_DEBUG'
                     AND marker.owner_key = ar.identity_key
                    WHERE ar.accepted_at <= %s AND marker.owner_key IS NULL
                    ORDER BY ar.identity_key
                    LIMIT %s
                    """,
                    (cutoff, limit),
                )
            else:
                kinds = (
                    TRACE_KINDS if resource_class is ResourceClass.TRACE_DETAIL else FACTUAL_KINDS
                )
                await cursor.execute(
                    f"""
                    SELECT pe.effect_kind, pe.effect_key
                    FROM projection_effects pe
                    LEFT JOIN retention_expiry_markers marker
                      ON marker.resource_class = %s
                     AND marker.resource_kind = ({PUBLIC_KIND_SQL})
                     AND marker.owner_key = pe.effect_key
                    WHERE pe.effect_kind = ANY(%s)
                      AND pe.recorded_at <= %s
                      AND marker.owner_key IS NULL
                    ORDER BY ({PUBLIC_KIND_SQL}), pe.effect_key
                    LIMIT %s
                    """,
                    (resource_class.value, list(kinds), cutoff, limit),
                )
            rows = await cursor.fetchall()
        return ExpiryBatch.create(
            resource_class=resource_class,
            policy_revision=policy_revision,
            cutoff=cutoff,
            members=tuple(
                ExpiryOwner(
                    resource_kind=("RAW_DEBUG" if row[0] == "RAW_DEBUG" else PUBLIC_KINDS[row[0]]),
                    owner_key=tuple(json.loads(row[1])),
                )
                for row in rows
            ),
        )

    async def apply_expiry(self, *, batch: ExpiryBatch, clock_now: datetime) -> ExpiryResult:
        if batch.resource_class is ResourceClass.ACCEPTED_PROVENANCE:
            raise ValueError("accepted provenance cannot expire")
        expired = 0
        already_expired = 0
        async with self._pool.connection() as connection, connection.transaction():
            for member in batch.members:
                key = _key_json(member.owner_key)
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        (f"{batch.resource_class.value}:{member.resource_kind}:{key}",),
                    )
                    await cursor.execute(
                        """
                        SELECT 1 FROM retention_expiry_markers
                        WHERE resource_class = %s AND resource_kind = %s AND owner_key = %s
                        FOR UPDATE
                        """,
                        (batch.resource_class.value, member.resource_kind, key),
                    )
                    if await cursor.fetchone() is not None:
                        already_expired += 1
                        continue
                    if batch.resource_class is ResourceClass.RAW_DEBUG:
                        record = await self._raw_record(cursor, key)
                    else:
                        record = await self._projection_record(
                            cursor,
                            batch.resource_class,
                            member.resource_kind,
                            key,
                        )
                    if record is None:
                        raise RuntimeError(
                            "planned retention resource disappeared without tombstone"
                        )
                    await cursor.execute(
                        """
                        INSERT INTO retention_expiry_markers
                            (resource_class, owner_key, source_identity_kind,
                             source_identity_key, resource_kind, recorded_at, compatibility,
                             policy_revision, expired_at, batch_identity)
                        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                        """,
                        (
                            batch.resource_class.value,
                            key,
                            record[0],
                            record[1],
                            member.resource_kind,
                            record[3],
                            json.dumps(record[4], separators=(",", ":")),
                            batch.policy_revision,
                            clock_now,
                            batch.batch_identity,
                        ),
                    )
                    if batch.resource_class is ResourceClass.RAW_DEBUG:
                        await cursor.execute(
                            "UPDATE accepted_records SET logical_record = '{}'::jsonb "
                            "WHERE identity_kind = %s AND identity_key = %s",
                            (record[0], record[1]),
                        )
                    else:
                        await cursor.execute(
                            "UPDATE projection_effects SET payload = '{}'::jsonb "
                            "WHERE effect_kind = %s AND effect_key = %s",
                            (record[2], key),
                        )
                    expired += 1
        return ExpiryResult(
            batch_identity=batch.batch_identity,
            selected=len(batch.members),
            expired=expired,
            already_expired=already_expired,
        )

    @staticmethod
    async def _raw_record(cursor: Any, key: str) -> tuple[Any, ...] | None:
        await cursor.execute(
            """
            SELECT identity_kind, identity_key, 'RAW_DEBUG', accepted_at, '[]'::jsonb
            FROM accepted_records
            WHERE identity_key = %s
            FOR UPDATE
            """,
            (key,),
        )
        return cast(tuple[Any, ...] | None, await cursor.fetchone())

    @staticmethod
    async def _projection_record(
        cursor: Any, resource_class: ResourceClass, resource_kind: str, key: str
    ) -> tuple[Any, ...] | None:
        kinds = TRACE_KINDS if resource_class is ResourceClass.TRACE_DETAIL else FACTUAL_KINDS
        internal_kind = INTERNAL_KINDS[resource_kind]
        if internal_kind not in kinds:
            raise ValueError("resource kind does not belong to expiry class")
        await cursor.execute(
            """
            SELECT source_identity_kind, source_identity_key, effect_kind, recorded_at,
                   pe.effect_key, pe.payload, ar.family_schema
            FROM projection_effects pe
            JOIN accepted_records ar
              ON ar.identity_kind = pe.source_identity_kind
             AND ar.identity_key = pe.source_identity_key
            WHERE pe.effect_kind = %s AND pe.effect_key = %s
            FOR UPDATE OF pe
            """,
            (internal_kind, key),
        )
        rows = await cursor.fetchall()
        if not rows:
            return None
        source_kind, source_key, effect_kind, recorded_at, effect_key, payload, family = rows[0]
        compatibility: list[list[Any]] = [
            ["family_schema", family],
            ["event_name", None],
            ["completeness", None],
        ]
        if effect_kind == "factual_contribution":
            event_name = json.loads(effect_key)[0]
            attributes = payload.get("attributes", {})
            compatibility[1][1] = event_name
            compatibility[2][1] = attributes.get("agentops.summary.state")
            compatibility.extend(
                [field_id, attributes[name]]
                for field_id, name in COMPATIBILITY_DIMENSIONS.get(event_name, ())
                if name in attributes
            )
        if effect_kind == "delivery_root_binding":
            compatibility.append(["delivery_id", payload["delivery_id"]])
        return source_kind, source_key, effect_kind, recorded_at, compatibility
