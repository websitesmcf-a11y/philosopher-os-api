"""Unified LLM client — routes across Anthropic, DeepSeek, and OpenAI."""
import logging
from typing import Any, AsyncGenerator

from app.config import settings
from app.llm.types import LLMResponse, ToolCall  # noqa: F401  (re-exported)
from app.llm.anthropic_client import AnthropicClient
from app.llm.openai_client import OpenAIClient
from app.llm.deepseek_client import DeepSeekClient

logger = logging.getLogger(__name__)


class LLMClient:
    """Unified client routing to whichever provider has credentials.

    Provider priority in "auto" mode: Anthropic > DeepSeek > OpenAI.
    Explicit model names always win (claude-* -> Anthropic, gpt-* -> OpenAI,
    deepseek-* -> DeepSeek). On failure, falls back through the remaining
    configured providers.
    """

    def __init__(self, preferred_provider: str | None = None):
        self._providers: dict[str, Any] = {
            "anthropic": AnthropicClient(),
            "openai": OpenAIClient(),
            "deepseek": DeepSeekClient(),
        }
        self.preferred = preferred_provider or settings.default_llm_provider

    def _has_key(self, name: str) -> bool:
        return bool({
            "anthropic": settings.anthropic_api_key,
            "openai": settings.openai_api_key,
            "deepseek": settings.deepseek_api_key,
        }.get(name))

    def _auto_provider(self) -> str:
        for name in ("anthropic", "deepseek", "openai"):
            if self._has_key(name):
                return name
        return "anthropic"  # let the provider raise a clear auth error

    @property
    def active_provider(self) -> str:
        if self.preferred in self._providers and self._has_key(self.preferred):
            return self.preferred
        return self._auto_provider()

    def _select_provider(self, model: str | None) -> tuple[str, Any]:
        if model:
            if model.startswith("gpt"):
                return "openai", self._providers["openai"]
            if model.startswith(("claude", "anthropic")):
                return "anthropic", self._providers["anthropic"]
            if model.startswith("deepseek"):
                return "deepseek", self._providers["deepseek"]
        name = self.active_provider
        return name, self._providers[name]

    def _fallback_chain(self, exclude: str) -> list[tuple[str, Any]]:
        return [
            (name, client)
            for name, client in self._providers.items()
            if name != exclude and self._has_key(name)
        ]

    def _normalize_model(self, provider: str, model: str | None) -> str | None:
        """Drop a model override that belongs to a different provider."""
        if model is None:
            return None
        prefix_map = {"openai": "gpt", "anthropic": "claude", "deepseek": "deepseek"}
        if model.startswith(prefix_map.get(provider, "")):
            return model
        return None

    async def generate(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        stream: bool = False,
        request_id: str | None = None,
    ) -> LLMResponse:
        """Send a message to the LLM with automatic provider fallback."""
        name, client = self._select_provider(model)
        attempts = [(name, client)] + self._fallback_chain(exclude=name)
        last_err: Exception | None = None
        for prov_name, prov in attempts:
            try:
                return await prov.generate(
                    system=system,
                    messages=messages,
                    tools=tools,
                    model=self._normalize_model(prov_name, model),
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=stream,
                )
            except Exception as err:
                last_err = err
                logger.warning(f"Provider {prov_name} failed: {err}. Trying next provider.")
        raise last_err or RuntimeError("No LLM provider configured")

    async def generate_stream(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> AsyncGenerator[str | dict, None]:
        """Yields text deltas as they arrive from the streaming LLM provider.

        If the model requests tool calls, a final ``{"__tool_calls__": [...]}``
        dict is yielded after the text deltas.
        """
        name, client = self._select_provider(model)
        attempts = [(name, client)] + self._fallback_chain(exclude=name)
        last_err: Exception | None = None
        for prov_name, prov in attempts:
            if not hasattr(prov, "generate_stream"):
                continue
            try:
                yielded = False
                async for delta in prov.generate_stream(
                    system=system,
                    messages=messages,
                    tools=tools,
                    model=self._normalize_model(prov_name, model),
                    max_tokens=max_tokens,
                    temperature=temperature,
                ):
                    yielded = True
                    yield delta
                return
            except Exception as err:
                if yielded:
                    # Mid-stream failure: cannot cleanly fall back, re-raise
                    raise
                last_err = err
                logger.warning(f"Stream provider {prov_name} failed: {err}. Trying next provider.")
        raise last_err or RuntimeError("No streaming LLM provider configured")

    def build_messages(
        self,
        user_input: str,
        context: str | None = None,
        history: list[dict] | None = None,
    ) -> list[dict]:
        """Build a message list from user input, optional context, and history."""
        msgs = list(history or [])
        content = ""
        if context:
            content += f"Context:\n{context}\n\n"
        content += user_input
        msgs.append({"role": "user", "content": content})
        return msgs

    def reset_providers(self) -> None:
        """Drop cached SDK clients so newly saved API keys take effect."""
        for prov in self._providers.values():
            prov._client = None


llm = LLMClient()
