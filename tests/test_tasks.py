"""Tests for tasks endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_tasks_empty(client: AsyncClient):
    response = await client.get("/api/v1/tasks")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data


@pytest.mark.asyncio
async def test_create_task(client: AsyncClient):
    response = await client.post("/api/v1/tasks", json={
        "title": "Complete project report",
        "priority": "high",
        "status": "pending",
    })
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Complete project report"
    assert data["status"] == "pending"
    assert data["priority"] == "high"


@pytest.mark.asyncio
async def test_complete_task(client: AsyncClient):
    create_resp = await client.post("/api/v1/tasks", json={
        "title": "Review code",
        "priority": "medium",
    })
    task_id = create_resp.json()["id"]

    response = await client.post(f"/api/v1/tasks/{task_id}/complete")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_delete_task(client: AsyncClient):
    create_resp = await client.post("/api/v1/tasks", json={
        "title": "Temp task",
        "priority": "low",
    })
    task_id = create_resp.json()["id"]

    response = await client.delete(f"/api/v1/tasks/{task_id}")
    assert response.status_code == 200
