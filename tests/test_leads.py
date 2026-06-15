"""Tests for lead CRUD endpoints."""

import pytest


@pytest.mark.asyncio
async def test_create_lead(client):
    payload = {
        "name": "Test Lead",
        "email": "test@example.com",
        "company": "TestCo",
        "source": "website",
    }
    response = await client.post("/api/v1/leads/", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Lead"
    assert data["email"] == "test@example.com"


@pytest.mark.asyncio
async def test_list_leads(client):
    response = await client.get("/api/v1/leads/")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_get_lead(client):
    # First create one
    create_resp = await client.post("/api/v1/leads/", json={"name": "Get Me"})
    assert create_resp.status_code == 201
    lead_id = create_resp.json()["id"]

    response = await client.get(f"/api/v1/leads/{lead_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Get Me"


@pytest.mark.asyncio
async def test_update_lead(client):
    create_resp = await client.post("/api/v1/leads/", json={"name": "Update Me"})
    lead_id = create_resp.json()["id"]

    response = await client.patch(f"/api/v1/leads/{lead_id}", json={"score": 95})
    assert response.status_code == 200
    assert response.json()["score"] == 95


@pytest.mark.asyncio
async def test_delete_lead(client):
    create_resp = await client.post("/api/v1/leads/", json={"name": "Delete Me"})
    lead_id = create_resp.json()["id"]

    response = await client.delete(f"/api/v1/leads/{lead_id}")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_create_lead_missing_name(client):
    response = await client.post("/api/v1/leads/", json={})
    assert response.status_code == 422
