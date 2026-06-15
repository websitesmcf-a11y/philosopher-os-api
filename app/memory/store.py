"""Memory storage layer — writes to AgentMemory table with embeddings."""
import uuid
import logging
from datetime import datetime
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database.models import AgentMemory, KnowledgeBase, Message, Lead
from app.memory.embeddings import embeddings as emb_service

logger = logging.getLogger(__name__)


class MemoryStore:
    """Stores memories with embeddings for semantic retrieval + batch operations."""

    def __init__(self, db: AsyncSession, org_id: str):
        self.db = db
        self.org_id = org_id

    async def store_agent_memory(
        self,
        agent_name: str,
        content: str,
        memory_type: str = "insight",
        importance: float = 0.5,
        metadata: dict | None = None,
    ) -> dict:
        """Store a memory entry with embedding."""
        try:
            embedding = await emb_service.embed(content)
        except Exception as e:
            logger.warning(f"Embedding failed for memory, storing without: {e}")
            embedding = None

        memory = AgentMemory(
            id=uuid.uuid4(),
            org_id=uuid.UUID(self.org_id) if isinstance(self.org_id, str) else self.org_id,
            agent_name=agent_name,
            memory_type=memory_type,
            content=content,
            extra_metadata=metadata or {},
            importance=importance,
            embedding=embedding,
            accessed_at=datetime.utcnow(),
        )
        self.db.add(memory)
        await self.db.flush()

        return {
            "id": str(memory.id),
            "agent_name": agent_name,
            "memory_type": memory_type,
            "importance": importance,
        }

    async def store_knowledge(
        self,
        title: str,
        content: str,
        category: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """Store a knowledge base entry with embedding."""
        try:
            embedding = await emb_service.embed(f"{title}\n{content}")
        except Exception as e:
            logger.warning(f"Embedding failed for knowledge, storing without: {e}")
            embedding = None

        entry = KnowledgeBase(
            id=uuid.uuid4(),
            org_id=uuid.UUID(self.org_id) if isinstance(self.org_id, str) else self.org_id,
            title=title,
            content=content,
            category=category,
            tags=tags or [],
            embedding=embedding,
        )
        self.db.add(entry)
        await self.db.flush()

        return {"id": str(entry.id), "title": title, "category": category}

    async def store_message_embedding(self, message_id: str, body: str) -> None:
        """Store or update a message's embedding for searchability."""
        try:
            embedding = await emb_service.embed(body)
        except Exception as e:
            logger.warning(f"Embedding failed for message {message_id}: {e}")
            return

        try:
            result = await self.db.execute(
                select(Message).where(Message.id == uuid.UUID(message_id))
            )
        except (ValueError, Exception):
            result = await self.db.execute(
                select(Message).where(Message.id == message_id)
            )
        msg = result.scalar_one_or_none()
        if msg:
            msg.embedding = embedding
            await self.db.flush()

    async def store_batch(self, entries: list[dict]) -> list[dict]:
        """Batch store multiple memory entries."""
        results = []
        for entry in entries:
            result = await self.store_agent_memory(
                agent_name=entry.get("agent_name", "system"),
                content=entry.get("content", ""),
                memory_type=entry.get("memory_type", "insight"),
                importance=entry.get("importance", 0.5),
                metadata=entry.get("metadata"),
            )
            results.append(result)
        return results
