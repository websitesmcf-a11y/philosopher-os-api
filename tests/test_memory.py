"""Tests for the memory/embedding system against the real interfaces."""

import uuid
from unittest.mock import AsyncMock, patch
import pytest

from app.memory.embeddings import EmbeddingService, EmbeddingError
from app.memory.store import MemoryStore
from app.memory.search import MemorySearch

ORG_ID = "00000000-0000-0000-0000-000000000001"


@pytest.mark.asyncio
async def test_embedding_generation_returns_vector():
    """embed() returns the vector produced by the provider."""
    svc = EmbeddingService()
    fake_vector = [0.1] * 1536
    with patch.object(EmbeddingService, "client", new_callable=lambda: property(lambda self: object())):
        with patch.object(svc, "_call_openai", AsyncMock(return_value=[fake_vector])):
            result = await svc.embed("Test text")
    assert result == fake_vector


@pytest.mark.asyncio
async def test_embedding_failure_raises_error():
    """Without an OpenAI key, embed() raises EmbeddingError (no silent zeros)."""
    svc = EmbeddingService()
    svc._openai_client = None
    with patch("app.memory.embeddings.settings") as mock_settings:
        mock_settings.openai_api_key = None
        with pytest.raises(EmbeddingError):
            await svc.embed("Should fail")


@pytest.mark.asyncio
async def test_store_agent_memory_persists(test_session):
    """store_agent_memory writes a row even when embeddings are unavailable."""
    store = MemoryStore(test_session, org_id=ORG_ID)
    result = await store.store_agent_memory(
        agent_name="socrates",
        content="The unexamined lead is not worth pursuing.",
        memory_type="insight",
        importance=0.9,
    )
    assert result["agent_name"] == "socrates"
    assert uuid.UUID(result["id"])  # valid UUID returned


@pytest.mark.asyncio
async def test_store_knowledge_persists(test_session):
    store = MemoryStore(test_session, org_id=ORG_ID)
    result = await store.store_knowledge(
        title="Pricing playbook",
        content="Always anchor high.",
        category="sales",
        tags=["pricing"],
    )
    assert result["title"] == "Pricing playbook"


@pytest.mark.asyncio
async def test_memory_search_fts_fallback(test_session):
    """Without embeddings, search falls back to FTS and still returns results."""
    store = MemoryStore(test_session, org_id=ORG_ID)
    await store.store_agent_memory(
        agent_name="plato",
        content="Quarterly revenue target is 50k.",
        memory_type="fact",
    )
    search = MemorySearch(test_session, org_id=ORG_ID)
    results = await search.search_memories("revenue target")
    assert isinstance(results, list)
    assert any("revenue" in r["content"].lower() for r in results)
