import uuid
import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, text
from app.database.models import KnowledgeBase
from app.schemas.knowledge import KnowledgeBaseCreate, KnowledgeBaseUpdate

logger = logging.getLogger(__name__)


class KnowledgeService:
    def __init__(self, db: AsyncSession, org_id: str):
        self.db = db
        self.org_id = org_id

    async def search(self, query_str: str, page: int = 1, page_size: int = 20, **filters):
        query = select(KnowledgeBase).where(KnowledgeBase.org_id == self.org_id)
        if query_str:
            like = f"%{query_str}%"
            query = query.where(
                or_(
                    KnowledgeBase.title.ilike(like),
                    KnowledgeBase.content.ilike(like),
                )
            )
        if filters.get("category"):
            query = query.where(KnowledgeBase.category == filters["category"])
        if filters.get("tags"):
            query = query.where(KnowledgeBase.tags.overlap(filters["tags"]))

        count_q = select(func.count()).select_from(query.subquery())
        total = (await self.db.execute(count_q)).scalar() or 0

        query = query.order_by(KnowledgeBase.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        items = result.scalars().all()

        return {
            "items": [self._to_response(e) for e in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def semantic_search(self, query_str: str, limit: int = 10):
        """PGVector similarity search with FTS fallback."""
        try:
            from app.memory.embeddings import EmbeddingService
            emb = await EmbeddingService().embed(query_str)
            if emb and len(emb) > 0:
                emb_str = "[" + ",".join(str(v) for v in emb) + "]"
                sql = text(
                    "SELECT id, org_id, title, content, category, tags, "
                    "embedding <=> :emb AS distance "
                    "FROM knowledge_base WHERE org_id = :org_id "
                    "ORDER BY distance ASC LIMIT :lim"
                )
                result = await self.db.execute(
                    sql, {"emb": emb_str, "org_id": self.org_id, "lim": limit}
                )
                rows = result.fetchall()
                if rows:
                    return [
                        {
                            "id": str(r[0]),
                            "title": r[3],
                            "content": r[4],
                            "category": r[5],
                            "tags": r[6] or [],
                            "score": max(0.0, 1.0 - float(r[7])),
                        }
                        for r in rows
                    ]
        except Exception as e:
            logger.warning(f"Semantic search failed, falling back to FTS: {e}")

        # FTS fallback
        return await self._fts_fallback(query_str, limit)

    async def _fts_fallback(self, query_str: str, limit: int = 10):
        like = f"%{query_str}%"
        query = (
            select(KnowledgeBase)
            .where(
                KnowledgeBase.org_id == self.org_id,
                or_(
                    KnowledgeBase.title.ilike(like),
                    KnowledgeBase.content.ilike(like),
                ),
            )
            .limit(limit)
        )
        result = await self.db.execute(query)
        items = result.scalars().all()
        return [
            {
                "id": str(e.id),
                "title": e.title,
                "content": e.content,
                "category": e.category,
                "tags": e.tags or [],
                "score": 0.5,
            }
            for e in items
        ]

    async def add_entry(self, data: KnowledgeBaseCreate):
        entry = KnowledgeBase(
            id=uuid.uuid4(),
            org_id=uuid.UUID(self.org_id) if isinstance(self.org_id, str) else self.org_id,
            **data.model_dump(exclude_none=True),
        )
        self.db.add(entry)
        await self.db.flush()

        # Generate embedding in background
        try:
            from app.memory.embeddings import EmbeddingService
            emb = await EmbeddingService().embed(f"{data.title}\n{data.content}")
            if emb:
                entry.embedding = emb
        except Exception as e:
            logger.warning(f"Failed to generate embedding for knowledge entry: {e}")

        return self._to_response(entry)

    async def delete_entry(self, entry_id: str):
        result = await self.db.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == entry_id, KnowledgeBase.org_id == self.org_id)
        )
        entry = result.scalar_one_or_none()
        if not entry:
            from app.core.errors import NotFoundError
            raise NotFoundError("Knowledge entry not found")
        await self.db.delete(entry)

    def _to_response(self, entry: KnowledgeBase):
        return {
            "id": str(entry.id),
            "org_id": str(entry.org_id),
            "title": entry.title,
            "content": entry.content,
            "category": entry.category,
            "tags": entry.tags or [],
            "created_at": entry.created_at,
            "updated_at": entry.updated_at,
        }
