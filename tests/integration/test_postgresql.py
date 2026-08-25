import os

import psycopg
import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_supported_postgresql_is_reachable() -> None:
    database_url = os.environ.get("WSR_EVIDENCE_DATABASE_URL")
    if database_url is None:
        pytest.skip("WSR_EVIDENCE_DATABASE_URL is not configured")

    async with (
        await psycopg.AsyncConnection.connect(database_url) as connection,
        connection.cursor() as cursor,
    ):
        await cursor.execute("SHOW server_version_num")
        row = await cursor.fetchone()

    assert row is not None
    assert int(row[0]) >= 180000
