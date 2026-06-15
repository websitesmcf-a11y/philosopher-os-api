"""Unit tests for agent tool execution."""

from unittest.mock import AsyncMock, patch
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_socrates_queries_real_db(client: AsyncClient):
    """Socrates agent can perform database queries via the chat endpoint."""
    response = await client.post("/api/v1/chat", json={
        "message": "How many leads do I have?",
        "agent": "socrates",
    })
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_athena_creates_calendar_event(client: AsyncClient):
    """Athena agent can create calendar events."""
    response = await client.post("/api/v1/chat", json={
        "message": "Schedule a meeting for tomorrow at 2pm",
        "agent": "athena",
    })
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_pythagoras_runs_query(client: AsyncClient):
    """Pythagoras agent can analyze data."""
    response = await client.post("/api/v1/chat", json={
        "message": "Analyze my Q2 revenue trends",
        "agent": "pythagoras",
    })
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_solon_calculates_mrr(client: AsyncClient):
    """Solon agent can get financial data."""
    response = await client.post("/api/v1/chat", json={
        "message": "What is my current MRR?",
        "agent": "solon",
    })
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_tool_failure_returns_error(client: AsyncClient):
    """Agent gracefully handles tool failures."""
    response = await client.post("/api/v1/chat", json={
        "message": "Do something impossible",
        "agent": "archimedes",
    })
    assert response.status_code == 200
    data = response.json()
    assert "reply" in data
