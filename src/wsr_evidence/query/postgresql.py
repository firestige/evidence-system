"""PostgreSQL snapshot adapter for the read-only query boundary."""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from wsr_evidence.query.faults import SnapshotError, SnapshotFault
from wsr_evidence.query.model import QueryEffect
from wsr_evidence.storage.postgresql import PostgresStorage
from wsr_evidence.storage.read_model import (
    CORE_READ_MODEL_VERSION,
    DEFAULT_SNAPSHOT_LEASE_LIMIT,
    DEFAULT_SNAPSHOT_LEASE_TTL,
    QUERY_CONTRACT_REVISION,
    TASK_QUERY_CONTRACT_REVISION,
    TASK_READ_MODEL_VERSION,
    ExpiryRecord,
    OwnerKey,
    ResourceClass,
    SnapshotPage,
    TraceDetailState,
    TraceSummary,
)

FACT_EFFECT_KINDS = (
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
TRACE_EFFECT_KINDS = ("trace_node", "trace_parent_edge", "trace_link")
TASK_EFFECT_KINDS = ("task_declaration", "delivery_task_membership")
PUBLIC_FACT_KINDS = {
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
FACT_KIND_SQL = """CASE pe.effect_kind
    WHEN 'factual_contribution' THEN 'EVENT_CONTRIBUTION'
    WHEN 'finding_assertion' THEN 'FINDING_ASSERTION'
    WHEN 'finding_target' THEN 'FINDING_TARGET'
    WHEN 'finding_status' THEN 'FINDING_STATUS'
    WHEN 'finding_fix' THEN 'FINDING_FIX'
    WHEN 'finding_recheck' THEN 'FINDING_RECHECK'
    WHEN 'role_lineage' THEN 'ROLE_LINEAGE'
    WHEN 'delivery_root_binding' THEN 'DELIVERY_ROOT_BINDING'
    WHEN 'model_attribution' THEN 'MODEL_ATTRIBUTION'
END"""
TRACE_KIND_ORDER_SQL = """CASE pe.effect_kind
    WHEN 'trace_node' THEN 1
    WHEN 'trace_parent_edge' THEN 2
    WHEN 'trace_link' THEN 3
END"""
TRACE_PUBLIC_KIND_SQL = """CASE pe.effect_kind
    WHEN 'trace_node' THEN 'NODE'
    WHEN 'trace_parent_edge' THEN 'PARENT_EDGE'
    WHEN 'trace_link' THEN 'LINK'
END"""
FACT_ID_SQL = (
    "'fact:' || pe.source_identity_kind || ':' || pe.source_identity_key || ':' || "
    f"({FACT_KIND_SQL}) || ':' || pe.effect_key"
)
TRACE_ID_SQL = (
    "'trace:' || pe.source_identity_kind || ':' || pe.source_identity_key || ':' || "
    "pe.effect_kind || ':' || pe.effect_key"
)


@dataclass(slots=True)
class _SnapshotLease:
    connection: AsyncConnection[Any]
    snapshot_id: str
    query: str
    filters: tuple[tuple[str, str], ...]
    limit: int
    expires_at: datetime
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _query_effect(row: tuple[Any, ...]) -> QueryEffect:
    return QueryEffect(
        kind=row[0],
        key=tuple(json.loads(row[1])),
        payload=row[2],
        source_identity=(row[3], row[4]),
        recorded_at=row[5],
        accepted_digest=row[6],
        profile_version=row[7],
        family_schema=row[8],
    )


class PostgresQueryReadModel:
    """Holds bounded repeatable-read leases on the runtime's existing ten-connection pool."""

    def __init__(
        self,
        pool: AsyncConnectionPool[Any],
        *,
        lease_ttl: timedelta = DEFAULT_SNAPSHOT_LEASE_TTL,
        lease_limit: int = DEFAULT_SNAPSHOT_LEASE_LIMIT,
    ) -> None:
        if lease_ttl.microseconds or not timedelta(seconds=10) <= lease_ttl <= timedelta(
            seconds=300
        ):
            raise ValueError("snapshot lease TTL must be whole seconds in [10,300]")
        if not 1 <= lease_limit <= 8:
            raise ValueError("snapshot lease limit must be in [1,8]")
        self._pool = pool
        self._lease_ttl = lease_ttl
        self._lease_limit = lease_limit
        self._leases: dict[str, _SnapshotLease] = {}
        self._cursors: dict[str, tuple[str, tuple[Any, ...]]] = {}
        self._continuation_tokens: dict[tuple[Any, ...], str] = {}
        self._lease_guard = asyncio.Lock()

    @classmethod
    def from_storage(cls, storage: PostgresStorage) -> PostgresQueryReadModel:
        return cls(storage._pool)  # noqa: SLF001 -- one runtime pool is a frozen Wave 6 invariant.

    async def close(self) -> None:
        async with self._lease_guard:
            for snapshot_id in tuple(self._leases):
                await self._release_lease(snapshot_id)

    async def acquire_snapshot(
        self,
        *,
        query: str,
        filters: tuple[tuple[str, str], ...],
        limit: int,
        clock_now: datetime,
    ) -> SnapshotPage[QueryEffect]:
        if query not in {"FACTS", "TRACES", "TASKS"} or not 1 <= limit <= 200:
            raise SnapshotError(SnapshotFault.INVALID, "invalid snapshot query")
        async with self._lease_guard:
            await self._evict_expired(clock_now)
            if len(self._leases) >= self._lease_limit:
                raise SnapshotError(SnapshotFault.UNAVAILABLE, "snapshot lease capacity exhausted")
            try:
                connection = await self._pool.getconn()
                await connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            except Exception as error:
                if "connection" in locals():
                    await self._pool.putconn(connection)
                raise SnapshotError(
                    SnapshotFault.UNAVAILABLE, "snapshot pool unavailable"
                ) from error
            snapshot_id = secrets.token_urlsafe(24)
            lease = _SnapshotLease(
                connection=connection,
                snapshot_id=snapshot_id,
                query=query,
                filters=filters,
                limit=limit,
                expires_at=clock_now + self._lease_ttl,
            )
            self._leases[snapshot_id] = lease
        try:
            return await self._read_snapshot_page(lease, after=None)
        except Exception:
            async with self._lease_guard:
                await self._release_lease(snapshot_id)
            raise

    async def continue_snapshot(
        self, *, cursor: str, clock_now: datetime
    ) -> SnapshotPage[QueryEffect]:
        async with self._lease_guard:
            await self._evict_expired(clock_now)
            continuation = self._cursors.get(cursor)
            if continuation is None:
                raise SnapshotError(SnapshotFault.EXPIRED, "snapshot cursor is unavailable")
            snapshot_id, after = continuation
            lease = self._leases.get(snapshot_id)
            if lease is None:
                raise SnapshotError(SnapshotFault.EXPIRED, "snapshot lease expired")
        return await self._read_snapshot_page(lease, after=after)

    async def read_expiry(
        self,
        *,
        resource_class: ResourceClass,
        resource_kind: str,
        owner_key: OwnerKey,
        snapshot_id: str,
    ) -> ExpiryRecord | None:
        lease = self._leases.get(snapshot_id)
        if lease is None:
            raise SnapshotError(SnapshotFault.EXPIRED, "snapshot lease expired")
        async with lease.connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT source_identity_kind, source_identity_key, resource_kind, recorded_at,
                       compatibility, policy_revision, expires_at, expired_at
                FROM retention_expiry_markers
                WHERE resource_class = %s AND resource_kind = %s AND owner_key = %s
                """,
                (
                    resource_class.value,
                    resource_kind,
                    json.dumps(owner_key, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return ExpiryRecord(
            resource_class=resource_class,
            owner_key=owner_key,
            source_identity=(row[0], row[1]),
            resource_kind=row[2],
            recorded_at=row[3],
            compatibility=tuple(tuple(pair) for pair in row[4]),
            policy_revision=row[5],
            expires_at=row[6],
            expired_at=row[7],
        )

    async def read_manifest(self, *, manifest_digest: str) -> QueryEffect | None:
        async with self._pool.connection() as connection, connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT pe.effect_kind, pe.effect_key, pe.payload,
                       pe.source_identity_kind, pe.source_identity_key, pe.recorded_at,
                       ar.canonical_digest, ar.profile_version, ar.family_schema
                FROM projection_effects pe
                JOIN accepted_records ar
                  ON ar.identity_kind = pe.source_identity_kind
                 AND ar.identity_key = pe.source_identity_key
                WHERE pe.effect_kind = 'delivery_manifest'
                  AND pe.effect_key = %s
                """,
                (json.dumps((manifest_digest,), separators=(",", ":")),),
            )
            row = await cursor.fetchone()
        return None if row is None else _query_effect(row)

    async def summarize_traces(self, *, snapshot_id: str) -> tuple[TraceSummary, ...]:
        lease = self._leases.get(snapshot_id)
        if lease is None:
            raise SnapshotError(SnapshotFault.EXPIRED, "snapshot lease expired")
        if lease.query != "TRACES":
            raise SnapshotError(SnapshotFault.MISMATCH, "snapshot is not a Trace query")
        clauses = ["pe.effect_kind = ANY(%s)"]
        parameters: list[Any] = [list(TRACE_EFFECT_KINDS)]
        for name, value in lease.filters:
            if name in {"limit", "cursor"}:
                continue
            self._add_filter(lease.query, clauses, parameters, name, value)
        statement = f"""
            SELECT pe.effect_key::jsonb ->> 0 AS trace_id,
                   count(*) FILTER (WHERE marker.owner_key IS NULL) AS active_count,
                   count(*) FILTER (WHERE marker.owner_key IS NOT NULL) AS expired_count
            FROM projection_effects pe
            LEFT JOIN retention_expiry_markers marker
              ON marker.resource_class = 'TRACE_DETAIL'
             AND marker.resource_kind = ({TRACE_PUBLIC_KIND_SQL})
             AND marker.owner_key = pe.effect_key
            WHERE {" AND ".join(clauses)}
            GROUP BY trace_id
            ORDER BY trace_id
        """
        async with lease.lock, lease.connection.cursor() as cursor:
            await cursor.execute(statement, parameters)
            rows = await cursor.fetchall()
        return tuple(
            TraceSummary(
                trace_id=row[0],
                state=(
                    TraceDetailState.AVAILABLE
                    if row[1] and not row[2]
                    else TraceDetailState.PARTIAL
                    if row[1] and row[2]
                    else TraceDetailState.EXPIRED
                ),
            )
            for row in rows
        )

    async def release_snapshot(self, snapshot_id: str) -> None:
        async with self._lease_guard:
            await self._release_lease(snapshot_id)

    async def _evict_expired(self, clock_now: datetime) -> None:
        expired = [
            snapshot_id
            for snapshot_id, lease in self._leases.items()
            if clock_now >= lease.expires_at
        ]
        for snapshot_id in expired:
            await self._release_lease(snapshot_id)

    async def _release_lease(self, snapshot_id: str) -> None:
        lease = self._leases.pop(snapshot_id, None)
        if lease is None:
            return
        self._cursors = {
            token: continuation
            for token, continuation in self._cursors.items()
            if continuation[0] != snapshot_id
        }
        self._continuation_tokens = {
            key: token for key, token in self._continuation_tokens.items() if key[0] != snapshot_id
        }
        try:
            await lease.connection.execute("ROLLBACK")
        finally:
            await self._pool.putconn(lease.connection)

    async def _read_snapshot_page(
        self, lease: _SnapshotLease, *, after: tuple[Any, ...] | None
    ) -> SnapshotPage[QueryEffect]:
        async with lease.lock:
            rows = await self._snapshot_rows(lease, after=after)
        selected = rows[: lease.limit]
        next_cursor = None
        if len(rows) > lease.limit:
            last = selected[-1]
            next_after = (last[9], last[10], last[11])
            cache_key = (lease.snapshot_id, *next_after)
            next_cursor = self._continuation_tokens.get(cache_key)
            if next_cursor is None:
                next_cursor = secrets.token_urlsafe(32)
                self._continuation_tokens[cache_key] = next_cursor
                self._cursors[next_cursor] = (lease.snapshot_id, next_after)
        task_query = lease.query == "TASKS"
        return SnapshotPage(
            contract_revision=(
                TASK_QUERY_CONTRACT_REVISION if task_query else QUERY_CONTRACT_REVISION
            ),
            read_model_revision=TASK_READ_MODEL_VERSION if task_query else CORE_READ_MODEL_VERSION,
            snapshot_id=lease.snapshot_id,
            resources=tuple(_query_effect(row) for row in selected),
            next_cursor=next_cursor,
        )

    async def _snapshot_rows(
        self, lease: _SnapshotLease, *, after: tuple[Any, ...] | None
    ) -> list[tuple[Any, ...]]:
        task_membership = lease.query == "TASKS" and any(
            name == "task_id" for name, _ in lease.filters
        )
        kinds = (
            FACT_EFFECT_KINDS
            if lease.query == "FACTS"
            else TASK_EFFECT_KINDS[1:]
            if task_membership
            else TASK_EFFECT_KINDS[:1]
            if lease.query == "TASKS"
            else TRACE_EFFECT_KINDS
        )
        clauses = ["pe.effect_kind = ANY(%s)"]
        parameters: list[Any] = [list(kinds)]
        for name, value in lease.filters:
            if name in {"limit", "cursor"}:
                continue
            self._add_filter(lease.query, clauses, parameters, name, value)
        if lease.query == "TRACES" and any(name == "delivery_id" for name, _ in lease.filters):
            await self._enforce_trace_delivery_bound(lease)
        if lease.query == "TRACES":
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM retention_expiry_markers marker "
                "WHERE marker.resource_class = 'TRACE_DETAIL' "
                f"AND marker.resource_kind = ({TRACE_PUBLIC_KIND_SQL}) "
                "AND marker.owner_key = pe.effect_key)"
            )
        sort_columns = (
            ("pe.recorded_at", FACT_KIND_SQL, FACT_ID_SQL)
            if lease.query == "FACTS"
            else (
                '(pe.effect_key::jsonb ->> 1) COLLATE "C"',
                "0",
                "pe.effect_key",
            )
            if task_membership
            else ('(pe.effect_key::jsonb ->> 0) COLLATE "C"', "0", "pe.effect_key")
            if lease.query == "TASKS"
            else ("pe.effect_key::jsonb ->> 0", TRACE_KIND_ORDER_SQL, TRACE_ID_SQL)
        )
        if after is not None:
            clauses.append(f"({', '.join(sort_columns)}) > (%s, %s, %s)")
            parameters.extend(after)
        parameters.append(lease.limit + 1)
        task_list = lease.query == "TASKS" and not task_membership
        payload_sql = (
            "pe.payload || CASE WHEN display.effect_key IS NULL THEN '{}'::jsonb ELSE "
            "jsonb_build_object('display_name', display.payload ->> 'display_name') END"
            if task_list
            else "pe.payload"
        )
        source_kind_sql = (
            "COALESCE(display.source_identity_kind, pe.source_identity_kind)"
            if task_list
            else "pe.source_identity_kind"
        )
        source_key_sql = (
            "COALESCE(display.source_identity_key, pe.source_identity_key)"
            if task_list
            else "pe.source_identity_key"
        )
        recorded_at_sql = (
            "COALESCE(display.recorded_at, pe.recorded_at)" if task_list else "pe.recorded_at"
        )
        display_join = (
            "LEFT JOIN projection_effects display ON display.effect_kind = "
            "'task_display_name' AND display.effect_key = pe.effect_key"
            if task_list
            else ""
        )
        statement = f"""
            SELECT pe.effect_kind, pe.effect_key, {payload_sql}, {source_kind_sql},
                   {source_key_sql}, {recorded_at_sql}, ar.canonical_digest,
                   ar.profile_version, ar.family_schema,
                   {sort_columns[0]} AS sort_a,
                   {sort_columns[1]} AS sort_b,
                   {sort_columns[2]} AS sort_c
            FROM projection_effects pe
            {display_join}
            JOIN accepted_records ar
              ON ar.identity_kind = {source_kind_sql}
             AND ar.identity_key = {source_key_sql}
            WHERE {" AND ".join(clauses)}
            ORDER BY sort_a, sort_b, sort_c
            LIMIT %s
        """
        async with lease.connection.cursor() as cursor:
            await cursor.execute(statement, parameters)
            return list(await cursor.fetchall())

    @staticmethod
    def _add_filter(
        query: str,
        clauses: list[str],
        parameters: list[Any],
        name: str,
        value: str,
    ) -> None:
        if name == "kind":
            reverse = {public: internal for internal, public in PUBLIC_FACT_KINDS.items()}
            internal = reverse.get(value)
            if internal is None:
                raise SnapshotError(SnapshotFault.INVALID, "unknown fact kind")
            clauses.append("pe.effect_kind = %s")
            parameters.append(internal)
        elif name == "event_name":
            clauses.append(
                "pe.effect_kind = 'factual_contribution' AND pe.effect_key::jsonb ->> 0 = %s"
            )
            parameters.append(value)
        elif name == "family_schema":
            clauses.append("ar.family_schema = %s")
            parameters.append(value)
        elif name == "trace_id":
            clauses.append(
                "pe.effect_key::jsonb ->> 0 = %s"
                if query == "TRACES"
                else (
                    "pe.source_identity_kind = 'span' AND pe.source_identity_key::jsonb ->> 1 = %s"
                )
            )
            parameters.append(value)
        elif name == "delivery_id":
            if query == "TRACES":
                clauses.append(
                    """pe.effect_key::jsonb ->> 0 IN (
                        SELECT root.effect_key::jsonb ->> 0
                        FROM projection_effects root
                        WHERE root.effect_kind = 'delivery_root_binding'
                          AND (
                              root.payload ->> 'delivery_id' = %s
                              OR EXISTS (
                                  SELECT 1 FROM retention_expiry_markers marker
                                  WHERE marker.resource_class = 'FACTUAL_PROJECTION'
                                    AND marker.owner_key = root.effect_key
                                    AND marker.resource_kind = 'DELIVERY_ROOT_BINDING'
                                    AND marker.compatibility @> %s::jsonb
                              )
                          )
                    )"""
                )
                parameters.extend((value, json.dumps([["delivery_id", value]])))
            else:
                clauses.append(
                    "pe.effect_kind = 'delivery_root_binding' AND "
                    "(pe.payload ->> 'delivery_id' = %s OR EXISTS ("
                    "SELECT 1 FROM retention_expiry_markers marker "
                    "WHERE marker.resource_class = 'FACTUAL_PROJECTION' "
                    "AND marker.owner_key = pe.effect_key "
                    "AND marker.resource_kind = 'DELIVERY_ROOT_BINDING' "
                    "AND marker.compatibility @> %s::jsonb))"
                )
                parameters.extend((value, json.dumps([["delivery_id", value]])))
        elif name in {"recorded_from", "recorded_to"}:
            operator = ">=" if name == "recorded_from" else "<="
            clauses.append(f"pe.recorded_at {operator} %s")
            parameters.append(value)
        elif query == "TASKS" and name == "task_id":
            clauses.append("pe.effect_key::jsonb ->> 0 = %s")
            parameters.append(value)
        elif query == "TASKS" and name == "as_of":
            clauses.append("pe.recorded_at <= %s::timestamptz")
            parameters.append(value)
        else:
            raise SnapshotError(SnapshotFault.INVALID, "unsupported snapshot filter")

    async def _enforce_trace_delivery_bound(self, lease: _SnapshotLease) -> None:
        delivery_id = next(value for name, value in lease.filters if name == "delivery_id")
        async with lease.connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT count(DISTINCT pe.effect_key::jsonb ->> 0)
                FROM projection_effects pe
                WHERE pe.effect_kind = ANY(%s)
                  AND pe.effect_key::jsonb ->> 0 IN (
                    SELECT root.effect_key::jsonb ->> 0
                    FROM projection_effects root
                    WHERE root.effect_kind = 'delivery_root_binding'
                      AND (
                          root.payload ->> 'delivery_id' = %s
                          OR EXISTS (
                              SELECT 1 FROM retention_expiry_markers marker
                              WHERE marker.resource_class = 'FACTUAL_PROJECTION'
                                AND marker.owner_key = root.effect_key
                                AND marker.resource_kind = 'DELIVERY_ROOT_BINDING'
                                AND marker.compatibility @> %s::jsonb
                          )
                      )
                  )
                """,
                (
                    list(TRACE_EFFECT_KINDS),
                    delivery_id,
                    json.dumps([["delivery_id", delivery_id]]),
                ),
            )
            row = await cursor.fetchone()
        if row is None or row[0] > 32:
            raise SnapshotError(
                SnapshotFault.BOUND_EXCEEDED,
                "delivery traversal exceeds the published Trace bound",
            )
