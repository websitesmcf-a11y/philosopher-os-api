"""Tests for chat endpoints (LLM patched — no live API calls)."""

from unittest.mock import AsyncMock, patch
import pytest
from httpx import AsyncClient

from app.llm.types import LLMResponse

CANNED = LLMResponse(content="The council has considered your question.", model="test")


@pytest.mark.asyncio
async def test_chat_requires_message(client: AsyncClient):
    response = await client.post("/api/v1/chat", json={})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_endpoint(client: AsyncClient):
    with patch("app.llm.client.LLMClient.generate", AsyncMock(return_value=CANNED)):
        response = await client.post("/api/v1/chat", json={
            "message": "What's the status of my campaigns?",
            "agent": "plato",
        })
    assert response.status_code == 200
    data = response.json()
    assert "reply" in data


@pytest.mark.asyncio
async def test_chat_returns_agent_and_conversation(client: AsyncClient):
    with patch("app.llm.client.LLMClient.generate", AsyncMock(return_value=CANNED)):
        response = await client.post("/api/v1/chat", json={
            "message": "Follow up question",
            "agent": "plato",
        })
    assert response.status_code == 200
    data = response.json()
    assert "conversation_id" in data
    assert "agent" in data
