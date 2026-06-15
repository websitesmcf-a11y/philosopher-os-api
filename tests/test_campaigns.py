"""Tests for campaign CRUD endpoints."""

import pytest


@pytest.mark.asyncio
async def test_create_campaign(client):
    payload = {
        "name": "Test Campaign",
        "channel": "email",
        "message_template": "Hello {{name}}!",
    }
    response = await client.post("/api/v1/campaigns/", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Campaign"
    assert data["status"] == "draft"


@pytest.mark.asyncio
async def test_list_campaigns(client):
    response = await client.get("/api/v1/campaigns/")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
