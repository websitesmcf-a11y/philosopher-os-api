"""Tests for agents endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_agents(client: AsyncClient):
    response = await client.get("/api/v1/agents")
    assert response.status_code == 200
    data = response.json()
    # Agents list should contain the philosopher council
    assert len(data) >= 1


@pytest.mark.asyncio
async def test_agent_status(client: AsyncClient):
    response = await client.get("/api/v1/agents/status")
    assert response.status_code == 200
    data = response.json()
    assert "agents" in data
    assert len(data["agents"]) >= 1


@pytest.mark.asyncio
async def test_agent_briefing(client: AsyncClient):
    response = await client.get("/api/v1/agents/plato/briefing")
    assert response.status_code == 200
