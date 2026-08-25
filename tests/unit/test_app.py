import pytest
from httpx import ASGITransport, AsyncClient

from wsr_evidence.app import create_app


@pytest.mark.asyncio
async def test_health_endpoint_reports_ready_without_authentication() -> None:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://evidence.test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "www-authenticate" not in response.headers
