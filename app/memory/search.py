"""Semantic search over the memory system using pgvector with FTS fallback."""
import logging
import uuid as _uuid
from typing import Any
from sqlalchemy import select, text, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.memory.embeddings import embeddings as emb_service, EmbeddingError

logger = logging.getLogger(__name__)


class MemorySearch:
    """Search across all memory types using vector similarity + FTS fallback."""

    def __init__(self, db: AsyncSession, org_id: str):
        self.db = db
        self._raw_org_id = str(org_id)

    @property
    def org_id(self) -> str:
        """Org ID formatted for raw-SQL binds on the active dialect.

        SQLite stores UUIDs as 32-char hex (no dashes); PostgreSQL uses the
        dashed canonical form.
        """
        try:
            dialect = self.db.get_bind().dialect.name
        except Exception:
            dialect = ""
        if dialect == "sqlite":
            try:
                return _uuid.UUID(self._raw_org_id).hex
            except ValueError:
                return self._raw_org_id
        return self._raw_org_id

    async def _vector_search(self, sql: str, params: dict) -> list | None:
        """Attempt vector search, return None on failure for FTS fallback."""
        try:
            result = await self.db.execute(text(sql), params)
            return result.fetchall()
        except Exception as e:
            logger.warning(f"Vector search failed, will fall back to FTS: {e}")
            return None

    async def _fts_search_memories(self, query: str, agent_name=None, memory_type=None, limit=10, min_importance=0.0):
        """Full-text search fallback for agent_memory."""
        like = f"%{query}%"
        stmt = select(
            text("id, agent_name, memory_type, content, metadata, importance, accessed_at")
        ).select_from(text("agent_memory"))
        stmt = stmt.where(text("org_id = :org_id")).where(
            text("lower(content) LIKE lower(:query)")
        )
        params = {"org_id": self.org_id, "query": like}
        if agent_name:
            stmt = stmt.where(text("agent_name = :agent_name"))
            params["agent_name"] = agent_name
        if memory_type:
            stmt = stmt.where(text("memory_type = :memory_type"))
            params["memory_type"] = memory_type
        stmt = stmt.order_by(text("importance DESC")).limit(limit)
        result = await self.db.execute(stmt, params)
        return result.fetchall()

    async def _fts_search_knowledge(self, query: str, category=None, limit=10):
        like = f"%{query}%"
        stmt = select(text("id, title, content, category, tags")).select_from(text("knowledge_base"))
        stmt = stmt.where(text("org_id = :org_id")).where(
            text("(lower(title) LIKE lower(:query) OR lower(content) LIKE lower(:query))")
        )
        params = {"org_id": self.org_id, "query": like}
        if category:
            stmt = stmt.where(text("category = :category"))
            params["category"] = category
        stmt = stmt.limit(limit)
        result = await self.db.execute(stmt, params)
        return result.fetchall()

    async def search_memories(
        self,
        query: str,
        agent_name: str | None = None,
        memory_type: str | None = None,
        limit: int = 10,
        min_importance: float = 0.0,
    ) -> list[dict]:
        """Semantic search across agent memories with FTS fallback."""
        try:
            embedding = await emb_service.embed(query)
        except EmbeddingError:
            logger.warning("Embedding unavailable, using FTS for memory search")
            rows = await self._fts_search_memories(query, agent_name, memory_type, limit, min_importance)
            return self._format_fts_memory_rows(rows)

        sql = """
            SELECT id, agent_name, memory_type, content, metadata, importance, accessed_at,
                   1 - (embedding <=> :embedding) AS similarity
            FROM agent_memory
            WHERE org_id = :org_id
              AND importance >= :min_importance
        """
        params = {
            "embedding": str(embedding),
            "org_id": self.org_id,
            "min_importance": min_importance,
        }

        if agent_name:
            sql += " AND agent_name = :agent_name"
            params["agent_name"] = agent_name
        if memory_type:
            sql += " AND memory_type = :memory_type"
            params["memory_type"] = memory_type

        sql += " ORDER BY similarity DESC LIMIT :limit"
        params["limit"] = limit

        rows = await self._vector_search(sql, params)
        if rows is None:
            rows = await self._fts_search_memories(query, agent_name, memory_type, limit, min_importance)
            return self._format_fts_memory_rows(rows)

        return self._format_vector_memory_rows(rows)

    async def search_knowledge(
        self,
        query: str,
        category: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Semantic search across knowledge base with FTS fallback."""
        try:
            embedding = await emb_service.embed(query)
        except EmbeddingError:
            logger.warning("Embedding unavailable, using FTS for knowledge search")
            rows = await self._fts_search_knowledge(query, category, limit)
            return self._format_fts_knowledge_rows(rows)

        sql = """
            SELECT id, title, content, category, tags,
                   1 - (embedding <=> :embedding) AS similarity
            FROM knowledge_base
            WHERE org_id = :org_id
        """
        params = {"embedding": str(embedding), "org_id": self.org_id}

        if category:
            sql += " AND category = :category"
            params["category"] = category

        sql += " ORDER BY similarity DESC LIMIT :limit"
        params["limit"] = limit

        rows = await self._vector_search(sql, params)
        if rows is None:
            rows = await self._fts_search_knowledge(query, category, limit)
            return self._format_fts_knowledge_rows(rows)

        return self._format_vector_knowledge_rows(rows)

    async def find_similar_leads(self, query: str, limit: int = 10) -> list[dict]:
        """Find leads similar to a description or query."""
        try:
            embedding = await emb_service.embed(query)
        except EmbeddingError:
            return []

        sql = """
            SELECT id, name, company, industry, status, score,
                   1 - (embedding <=> :embedding) AS similarity
            FROM leads
            WHERE org_id = :org_id
            ORDER BY similarity DESC
            LIMIT :limit
        """
        result = await self.db.execute(
            text(sql),
            {"embedding": str(embedding), "org_id": self.org_id, "limit": limit},
        )
        rows = result.fetchall()
        return [
            {
                "id": str(r[0]),
                "name": r[1],
                "company": r[2],
                "industry": r[3],
                "status": r[4],
                "score": r[5],
                "similarity": float(r[6]) if r[6] else 0.0,
            }
            for r in rows
        ]

    async def search(self, query: str, agent_name: str | None = None, limit: int = 10) -> list[dict]:
        """Shorthand for agent tool calls — delegates to search_memories."""
        return await self.search_memories(query, agent_name=agent_name, limit=limit)

    async def hybrid_search(self, query: str, limit: int = 10) -> list[dict]:
        """Combined search across all memory sources."""
        memories = await self.search_memories(query, limit=limit)
        knowledge = await self.search_knowledge(query, limit=limit)
        leads = await self.find_similar_leads(query, limit=limit)
        return {"memories": memories, "knowledge": knowledge, "similar_leads": leads, "total": len(memories) + len(knowledge) + len(leads)}

    def _format_vector_memory_rows(self, rows) -> list[dict]:
        return [
            {
                "id": str(r[0]), "agent_name": r[1], "memory_type": r[2],
                "content": r[3], "metadata": r[4] if isinstance(r[4], dict) else {},
                "importance": float(r[5]) if r[5] else 0.0,
                "accessed_at": str(r[6]) if r[6] else None,
                "similarity": float(r[7]) if r[7] else 0.0,
            }
            for r in rows
        ]

    def _format_fts_memory_rows(self, rows) -> list[dict]:
        return [
            {
                "id": str(r[0]), "agent_name": r[1], "memory_type": r[2],
                "content": r[3], "metadata": r[4] if isinstance(r[4], dict) else {},
                "importance": float(r[5]) if r[5] else 0.0,
                "accessed_at": str(r[6]) if r[6] else None,
                "similarity": 0.5,
            }
            for r in rows
        ]

    def _format_vector_knowledge_rows(self, rows) -> list[dict]:
        return [
            {"id": str(r[0]), "title": r[1], "content": r[2], "category": r[3], "tags": r[4] if r[4] else [], "similarity": float(r[5]) if r[5] else 0.0}
            for r in rows
        ]

    def _format_fts_knowledge_rows(self, rows) -> list[dict]:
        return [
            {"id": str(r[0]), "title": r[1], "content": r[2], "category": r[3], "tags": r[4] if r[4] else [], "similarity": 0.5}
            for r in rows
        ]
