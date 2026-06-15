"""Memory indexing workers — generate embeddings for semantic search."""
import logging
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def index_message_embedding(self, message_id: str):
    """Generate and store embedding for a message."""
    logger.info(f"Indexing embedding for message {message_id}")
    try:
        from sqlalchemy import create_engine, text
        from app.config import settings

        engine = create_engine(settings.supabase_db_url) if settings.supabase_db_url else None
        if not engine:
            return {"status": "skipped", "message_id": message_id, "note": "No DB URL"}

        with engine.connect() as conn:
            result = conn.execute(text("SELECT body FROM messages WHERE id = :mid"), {"mid": message_id})
            row = result.fetchone()
            if not row:
                return {"status": "not_found", "message_id": message_id}

            import openai
            openai.api_key = settings.openai_api_key
            resp = openai.embeddings.create(model=settings.embedding_model, input=row[0])
            embedding = resp.data[0].embedding

            conn.execute(
                text("UPDATE messages SET embedding = :emb WHERE id = :mid"),
                {"emb": str(embedding), "mid": message_id},
            )
            conn.commit()

        logger.info(f"Indexed embedding for message {message_id}")
        return {"status": "indexed", "message_id": message_id}
    except Exception as exc:
        logger.error(f"Failed to index message {message_id}: {exc}")
        try:
            self.retry(exc=exc)
        except Exception:
            return {"status": "failed", "message_id": message_id, "error": str(exc)}


@celery_app.task(bind=True, max_retries=2)
def index_knowledge_embedding(self, entry_id: str):
    """Generate and store embedding for a knowledge base entry."""
    logger.info(f"Indexing embedding for knowledge entry {entry_id}")
    try:
        from sqlalchemy import create_engine, text
        from app.config import settings

        engine = create_engine(settings.supabase_db_url) if settings.supabase_db_url else None
        if not engine:
            return {"status": "skipped", "entry_id": entry_id, "note": "No DB URL"}

        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT title, content FROM knowledge_base WHERE id = :eid"), {"eid": entry_id}
            )
            row = result.fetchone()
            if not row:
                return {"status": "not_found", "entry_id": entry_id}

            import openai
            openai.api_key = settings.openai_api_key
            resp = openai.embeddings.create(model=settings.embedding_model, input=f"{row[0]}\n{row[1]}")
            embedding = resp.data[0].embedding

            conn.execute(
                text("UPDATE knowledge_base SET embedding = :emb WHERE id = :eid"),
                {"emb": str(embedding), "eid": entry_id},
            )
            conn.commit()

        return {"status": "indexed", "entry_id": entry_id}
    except Exception as exc:
        logger.error(f"Failed to index knowledge {entry_id}: {exc}")
        try:
            self.retry(exc=exc)
        except Exception:
            return {"status": "failed", "entry_id": entry_id, "error": str(exc)}


@celery_app.task(bind=True, max_retries=2)
def consolidate_memory(self, org_id: str):
    """Consolidate recent interactions into permanent memories."""
    logger.info(f"Consolidating memory for org {org_id}")
    try:
        from sqlalchemy import create_engine, text
        from app.config import settings

        engine = create_engine(settings.supabase_db_url) if settings.supabase_db_url else None
        if not engine:
            return {"status": "skipped", "org_id": org_id}

        with engine.connect() as conn:
            # Summarize last 50 unconsolidated messages into a memory
            result = conn.execute(
                text("""
                    SELECT body FROM messages
                    WHERE conversation_id IN (
                        SELECT id FROM conversations WHERE org_id = :oid
                    )
                    ORDER BY created_at DESC LIMIT 50
                """),
                {"oid": org_id},
            )
            messages = [r[0] for r in result.fetchall() if r[0]]

            if messages:
                summary = " | ".join(messages[:10])[:2000]
                conn.execute(
                    text("""
                        INSERT INTO agent_memory (org_id, agent_name, memory_type, content, importance)
                        VALUES (:oid, 'system', 'consolidated', :content, 0.6)
                    """),
                    {"oid": org_id, "content": f"Consolidated conversation summary: {summary}"},
                )
                conn.commit()

        return {"status": "consolidated", "org_id": org_id, "messages_processed": len(messages) if messages else 0}
    except Exception as exc:
        logger.error(f"Memory consolidation failed: {exc}")
        try:
            self.retry(exc=exc)
        except Exception:
            return {"status": "failed", "org_id": org_id, "error": str(exc)}


@celery_app.task(bind=True, max_retries=2)
def prune_stale_memories(self, org_id: str):
    """Prune low-importance, old memories."""
    logger.info(f"Pruning stale memories for org {org_id}")
    try:
        from sqlalchemy import create_engine, text
        from datetime import datetime, timezone
        from app.config import settings

        engine = create_engine(settings.supabase_db_url) if settings.supabase_db_url else None
        if not engine:
            return {"status": "skipped", "org_id": org_id}

        with engine.connect() as conn:
            result = conn.execute(
                text("""
                    DELETE FROM agent_memory
                    WHERE org_id = :oid
                      AND importance < 0.3
                      AND created_at < :cutoff
                """),
                {"oid": org_id, "cutoff": datetime.now(timezone.utc).isoformat()},
            )
            deleted = result.rowcount
            conn.commit()

        logger.info(f"Pruned {deleted} stale memories for org {org_id}")
        return {"status": "pruned", "org_id": org_id, "deleted_count": deleted}
    except Exception as exc:
        logger.error(f"Memory pruning failed: {exc}")
        try:
            self.retry(exc=exc)
        except Exception:
            return {"status": "failed", "org_id": org_id, "error": str(exc)}
