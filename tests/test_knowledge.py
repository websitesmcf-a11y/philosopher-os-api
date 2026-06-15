"""Tests for knowledge base endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_search_empty(client: AsyncClient):
    response = await client.get("/api/v1/knowledge", params={"q": "nonexistent"})
    assert response.status_code == 200
    data = response.json()
    assert "items" in data


@pytest.mark.asyncio
async def test_add_entry(client: AsyncClient):
    response = await client.post("/api/v1/knowledge", json={
        "title": "Socrates AI Architecture",
        "content": "The system uses a council of agents pattern.",
        "category": "architecture",
        "tags": ["agents", "architecture"],
    })
    assert response.status_code in (200, 201)


@pytest.mark.asyncio
async def test_search_returns_entry(client: AsyncClient):
    await client.post("/api/v1/knowledge", json={
        "title": "FastAPI Best Practices",
        "content": "Use dependency injection for services.",
        "category": "development",
    })
    response = await client.get("/api/v1/knowledge", params={"q": "FastAPI"})
    data = response.json()
    results = [item for item in data["items"] if "FastAPI" in item.get("title", "")]
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_delete_entry(client: AsyncClient):
    create_resp = await client.post("/api/v1/knowledge", json={
        "title": "Temp Entry",
        "content": "Will be deleted",
    })
    item = create_resp.json()
    item_id = item.get("id")

    if item_id:
        response = await client.delete(f"/api/v1/knowledge/{item_id}")
        assert response.status_code in (200, 204)
