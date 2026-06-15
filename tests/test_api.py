"""Philosopher OS — API unit tests for critical endpoints."""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.database.session import async_session, engine
from app.database.models import Base


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
async def setup_db():
    """Create tables for testing, drop after."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.anyio
async def test_health_endpoint():
    """Health check should return 200."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/health/liveness")
        assert resp.status_code == 200
        assert resp.json()["status"] == "alive"


@pytest.mark.anyio
async def test_auth_me_endpoint():
    """Auth me should return user info (dev mode without token)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert "email" in data
        assert "name" in data


@pytest.mark.anyio
async def test_create_lead():
    """Create a lead and verify it's returned."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create lead
        resp = await client.post("/api/v1/leads/", json={"name": "Test Lead", "phone": "+27123456789"})
        assert resp.status_code == 201
        lead = resp.json()
        assert lead["name"] == "Test Lead"
        lead_id = lead["id"]

        # List leads
        resp = await client.get("/api/v1/leads/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1


@pytest.mark.anyio
async def test_create_campaign():
    """Create a campaign and verify."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/campaigns/", json={
            "name": "Test Campaign",
            "channel": "whatsapp",
            "message_template": "Hello {{name}}",
            "status": "draft",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Test Campaign"
        assert data["channel"] == "whatsapp"


@pytest.mark.anyio
async def test_create_task():
    """Create a task with 24h time format."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/tasks/", json={
            "title": "Test Task",
            "priority": "high",
            "due_date": "2026-06-15T19:20:00",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Test Task"
        # Verify 24-hour time is preserved
        assert "19:20" in data["due_date"]


@pytest.mark.anyio
async def test_edit_task():
    """Edit a task and verify changes."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create
        create = await client.post("/api/v1/tasks/", json={"title": "Original", "priority": "low"})
        task_id = create.json()["id"]

        # Edit (follow_redirects=True handles the trailing slash redirect)
        resp = await client.patch(f"/api/v1/tasks/{task_id}/", json={"title": "Updated", "priority": "critical"}, follow_redirects=True)
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Updated"
        assert data["priority"] == "critical"


@pytest.mark.anyio
async def test_lead_list_crud():
    """Test lead list creation."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/lead-lists/", json={"name": "Test Pool"}, follow_redirects=True)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Test Pool"
        assert "id" in data


@pytest.mark.anyio
async def test_analytics():
    """Analytics should return all required fields."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/analytics/dashboard/", follow_redirects=True)
        assert resp.status_code == 200
        data = resp.json()
        for field in ["total_leads", "messages_today", "active_campaigns", "conversion_rate"]:
            assert field in data, f"Missing field: {field}"
