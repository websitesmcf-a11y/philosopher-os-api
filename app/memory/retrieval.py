"""Context retrieval — builds structured context for agent prompts from memory."""
import logging
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from app.memory.search import MemorySearch

logger = logging.getLogger(__name__)


class ContextRetriever:
    """Retrieves and formats context for agent prompts."""

    def __init__(self, db: AsyncSession, org_id: str, agent_name: str):
        self.search = MemorySearch(db, org_id)
        self.org_id = org_id
        self.agent_name = agent_name

    async def build_context(self, query: str, depth: str = "standard") -> str:
        """Build a formatted context string from memory for agent consumption."""
        limit = 20 if depth == "deep" else 10

        result = await self.search.hybrid_search(query, limit=limit)

        parts = []

        if result["memories"]:
            parts.append("=== RELEVANT MEMORIES ===")
            for m in result["memories"][:8]:
                parts.append(f"[{m['agent_name']}] ({m['similarity']:.2f}) {m['content']}")

        if result["knowledge"]:
            parts.append("\n=== KNOWLEDGE BASE ===")
            for k in result["knowledge"][:5]:
                parts.append(f"[{k['category'] or 'General'}] {k['title']}: {k['content'][:300]}")

        if result["similar_leads"]:
            parts.append("\n=== SIMILAR LEADS ===")
            for l in result["similar_leads"][:3]:
                parts.append(f"{l['name']} ({l['company'] or 'N/A'}) — {l['status']} (score: {l['score']})")

        return "\n".join(parts) if parts else "No relevant context found."

    async def get_agent_history(self, limit: int = 10) -> list[dict]:
        """Get recent memories for this specific agent."""
        return await self.search.search_memories(
            query="recent activity",
            agent_name=self.agent_name,
            limit=limit,
        )
