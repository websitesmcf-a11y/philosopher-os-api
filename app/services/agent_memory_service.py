import uuid
import logging
from typing import Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete as sa_delete
from app.database.models import AgentMemory

logger = logging.getLogger(__name__)


class AgentMemoryService:
    def __init__(self, db: AsyncSession, org_id: str):
        self.db = db
        self.org_id = org_id

    async def get_memory(self, agent_name: str, memory_type: Optional[str] = None, limit: int = 50):
        query = select(AgentMemory).where(
            AgentMemory.org_id == self.org_id,
            AgentMemory.agent_name == agent_name,
        )
        if memory_type:
            query = query.where(AgentMemory.memory_type == memory_type)
        query = query.order_by(AgentMemory.created_at.desc()).limit(limit)

        result = await self.db.execute(query)
        items = result.scalars().all()
        return [self._to_response(m) for m in items]

    async def add_memory(
        self,
        agent_name: str,
        content: str,
        memory_type: str = "insight",
        importance: float = 0.5,
        metadata: dict = None,
    ):
        memory = AgentMemory(
            id=uuid.uuid4(),
            org_id=uuid.UUID(self.org_id) if isinstance(self.org_id, str) else self.org_id,
            agent_name=agent_name,
            memory_type=memory_type,
            content=content,
            extra_metadata=metadata or {},
            importance=importance,
        )
        self.db.add(memory)
        await self.db.flush()

        # Generate embedding in background
        try:
            from app.memory.embeddings import EmbeddingService
            emb = await EmbeddingService().embed(content)
            if emb:
                memory.embedding = emb
        except Exception as e:
            logger.warning(f"Failed to generate embedding for memory: {e}")

        return self._to_response(memory)

    async def get_briefing(self, agent_name: str) -> str:
        """Generate a concise briefing from recent high-importance memories."""
        query = (
            select(AgentMemory)
            .where(
                AgentMemory.org_id == self.org_id,
                AgentMemory.agent_name == agent_name,
                AgentMemory.importance >= 0.7,
            )
            .order_by(AgentMemory.created_at.desc())
            .limit(10)
        )
        result = await self.db.execute(query)
        items = result.scalars().all()

        if not items:
            return "No high-importance memories found."

        lines = []
        for m in items:
            lines.append(f"[{m.memory_type}] (importance: {m.importance:.1f}) {m.content}")
        return "\n".join(lines)

    async def delete_memory(self, memory_id: str):
        result = await self.db.execute(
            select(AgentMemory).where(AgentMemory.id == memory_id, AgentMemory.org_id == self.org_id)
        )
        memory = result.scalar_one_or_none()
        if not memory:
            from app.core.errors import NotFoundError
            raise NotFoundError("Memory not found")
        await self.db.delete(memory)

    def _to_response(self, memory: AgentMemory):
        return {
            "id": str(memory.id),
            "org_id": str(memory.org_id),
            "agent_name": memory.agent_name,
            "memory_type": memory.memory_type,
            "content": memory.content,
            "metadata": memory.extra_metadata or {},
            "importance": memory.importance,
            "accessed_at": memory.accessed_at,
            "created_at": memory.created_at,
            "updated_at": memory.updated_at,
        }
