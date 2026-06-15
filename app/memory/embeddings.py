"""Embedding generation for the AI memory system."""
import logging
from typing import Any
from tenacity import retry, stop_after_attempt, wait_exponential
from app.config import settings

logger = logging.getLogger(__name__)


class EmbeddingError(Exception):
    """Raised when embedding generation fails definitively."""


class EmbeddingService:
    """Generates text embeddings for semantic search with retry + fallback."""

    def __init__(self):
        self._openai_client = None
        self._dimension = 1536

    @property
    def client(self):
        if self._openai_client is None and settings.openai_api_key:
            from openai import AsyncOpenAI
            self._openai_client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=30.0)
        return self._openai_client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    async def _call_openai(self, inputs: list[str]) -> list[list[float]]:
        resp = await self.client.embeddings.create(
            model=settings.embedding_model,
            input=inputs,
        )
        return [d.embedding for d in resp.data]

    async def embed(self, text: str | list[str]) -> list[float] | list[list[float]]:
        """Generate embeddings for a single string or list of strings."""
        single = isinstance(text, str)
        inputs = [text] if single else text
        if not inputs:
            return []

        try:
            if self.client:
                embeddings = await self._call_openai(inputs)
            else:
                raise EmbeddingError("No OpenAI API key configured for embeddings")
            return embeddings[0] if single else embeddings
        except Exception as e:
            logger.error(f"Embedding generation failed after retries: {e}")
            raise EmbeddingError(f"Failed to generate embeddings: {e}") from e

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        """Batch embedding generation."""
        return await self.embed(texts)


embeddings = EmbeddingService()
