"""Ollama client — local AI fallback when all cloud models are rate-limited."""
import logging
from typing import AsyncGenerator

from app.llm.openai_client import OpenAIClient

logger = logging.getLogger(__name__)

# Local models in priority order (smarter first).
OLLAMA_WATERFALL = [
    "dolphin-llama3:latest",  # 8B — more capable
    "llama3.2:latest",        # 3B — final fallback
]


class OllamaClient(OpenAIClient):
    """Ollama provider using OpenAI-compatible /v1 API.

    Connects to a local or remote Ollama instance. No API key needed.
    Falls through the OLLAMA_WATERFALL on model-not-found or failure.
    """

    def __init__(self, base_url: str = "http://localhost:11434"):
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.api_key = "ollama"
        self.default_model = OLLAMA_WATERFALL[0]

    @property
    def client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=f"{self.base_url}/v1",
                timeout=180.0,
            )
        return self._client

    async def generate(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        stream: bool = False,
    ):
        models_to_try = [model] if model else OLLAMA_WATERFALL
        last_exc: Exception | None = None
        for m in models_to_try:
            try:
                result = await super().generate(
                    system=system,
                    messages=messages,
                    tools=tools,
                    model=m,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=stream,
                )
                logger.info("Ollama success: %s", m)
                return result
            except Exception as exc:
                logger.warning("Ollama model %s failed: %s", m, exc)
                last_exc = exc
        raise last_exc or RuntimeError("No Ollama model available")

    async def generate_stream(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> AsyncGenerator[str | dict, None]:
        models_to_try = [model] if model else OLLAMA_WATERFALL
        last_exc: Exception | None = None
        for m in models_to_try:
            yielded = False
            try:
                async for delta in super().generate_stream(
                    system=system,
                    messages=messages,
                    tools=tools,
                    model=m,
                    max_tokens=max_tokens,
                    temperature=temperature,
                ):
                    yielded = True
                    yield delta
                return
            except Exception as exc:
                if yielded:
                    raise
                logger.warning("Ollama stream model %s failed: %s", m, exc)
                last_exc = exc
        raise last_exc or RuntimeError("No Ollama model available")
