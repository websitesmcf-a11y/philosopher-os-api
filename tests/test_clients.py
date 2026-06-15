"""Tests for clients endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_clients_empty(client: AsyncClient):
    response = await client.get("/api/v1/clients")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data


@pytest.mark.asyncio
async def test_create_client(client: AsyncClient):
    response = await client.post("/api/v1/clients", json={
        "name": "Test Client Inc.",
        "email": "contact@testclient.com",
        "company": "Test Client Inc.",
        "industry": "Technology",
        "contract_status": "active",
        "mrr": 50000,
        "lifetime_value": 600000,
    })
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Client Inc."


@pytest.mark.asyncio
async def test_list_returns_created(client: AsyncClient):
    await client.post("/api/v1/clients", json={
        "name": "Another Client",
        "email": "hello@another.com",
        "contract_status": "active",
        "mrr": 25000,
        "lifetime_value": 300000,
    })
    response = await client.get("/api/v1/clients")
    data = response.json()
    assert len(data["items"]) >= 1
