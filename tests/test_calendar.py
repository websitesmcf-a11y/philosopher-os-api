"""Tests for calendar endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_event(client: AsyncClient):
    response = await client.post("/api/v1/calendar/events", json={
        "title": "Client Meeting",
        "event_type": "meeting",
        "start_time": "2026-06-15T10:00:00Z",
        "end_time": "2026-06-15T11:00:00Z",
        "description": "Discuss Q3 strategy",
    })
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Client Meeting"


@pytest.mark.asyncio
async def test_list_events(client: AsyncClient):
    await client.post("/api/v1/calendar/events", json={
        "title": "Review",
        "event_type": "internal",
        "start_time": "2026-06-16T14:00:00Z",
        "end_time": "2026-06-16T15:00:00Z",
    })
    response = await client.get("/api/v1/calendar/events")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) >= 1


@pytest.mark.asyncio
async def test_list_events_by_date_range(client: AsyncClient):
    response = await client.get("/api/v1/calendar/events", params={
        "start_date": "2026-06-01",
        "end_date": "2026-06-30",
    })
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_delete_event(client: AsyncClient):
    create_resp = await client.post("/api/v1/calendar/events", json={
        "title": "Temp Event",
        "event_type": "call",
        "start_time": "2026-06-17T09:00:00Z",
        "end_time": "2026-06-17T09:30:00Z",
    })
    event_id = create_resp.json()["id"]

    response = await client.delete(f"/api/v1/calendar/events/{event_id}")
    assert response.status_code == 200
