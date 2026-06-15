"""Tests for automation endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_rules_empty(client: AsyncClient):
    response = await client.get("/api/v1/automation/rules")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data


@pytest.mark.asyncio
async def test_create_rule(client: AsyncClient):
    response = await client.post("/api/v1/automation/rules", json={
        "name": "Auto-tag new leads",
        "trigger_event": "lead.created",
        "conditions": {},
        "actions": {"type": "tag_lead", "tag": "new"},
        "enabled": True,
    })
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Auto-tag new leads"


@pytest.mark.asyncio
async def test_delete_rule(client: AsyncClient):
    create_resp = await client.post("/api/v1/automation/rules", json={
        "name": "Temp rule",
        "trigger_event": "lead.updated",
        "actions": {"type": "notify"},
    })
    rule_id = create_resp.json()["id"]

    response = await client.delete(f"/api/v1/automation/rules/{rule_id}")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_list_jobs(client: AsyncClient):
    response = await client.get("/api/v1/automation/jobs")
    assert response.status_code == 200
