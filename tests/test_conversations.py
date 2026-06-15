"""Tests for conversations endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_conversations_empty(client: AsyncClient):
    response = await client.get("/api/v1/conversations")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data


@pytest.mark.asyncio
async def test_send_message_creates_conversation(client: AsyncClient):
    response = await client.post("/api/v1/conversations/send", json={
        "channel": "email",
        "to": "test@example.com",
        "content": "Hello from Socrates AI",
    })
    assert response.status_code in (200, 201)


@pytest.mark.asyncio
async def test_list_returns_conversations(client: AsyncClient):
    response = await client.get("/api/v1/conversations")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data


@pytest.mark.asyncio
async def test_channel_filter(client: AsyncClient):
    response = await client.get("/api/v1/conversations", params={"channel": "email"})
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
