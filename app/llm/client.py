"""Unified LLM client — routes across OpenRouter, Anthropic, DeepSeek, OpenAI, and Ollama."""
import logging
from typing import Any, AsyncGenerator

from app.config import settings
from app.llm.types import LLMResponse, ToolCall  # noqa: F401  (re-exported)

logger = logging.getLogger(__name__)


class LLMClient:
    """Unified client routing to the best available provider.

    Priority (auto mode):
      1. OpenRouter — smart free-model waterfall (11 cloud models, smartest first)
      2. Anthropic   — if api key is configured
      3. DeepSeek    — if api key is configured
      4. OpenAI      — if api key is configured
      5. Ollama      — local/remote fallback when all cloud models are rate-limited

    Explicit model names always override: claude-* → Anthropic, gpt-* → OpenAI,
    deepseek-* → DeepSeek, "org/model" format → OpenRouter.
    On any provider failure the next in the chain is tried automatically.
    """

    def __init__(self, preferred_provider: str | None = None):
        self._providers: dict[str, Any] = {}
        self.preferred = preferred_provider or settings.default_llm_provider
        self._build_providers()

    def _build_providers(self) -> None:
        from app.llm.anthropic_client import AnthropicClient
        from app.llm.openai_client import OpenAIClient
        from app.llm.deepseek_client import DeepSeekClient
        from app.llm.openrouter_client import OpenRouterClient
        from app.llm.ollama_client import OllamaClient

        self._providers = {
            "anthropic": AnthropicClient(),
            "openai": OpenAIClient(),
            "deepseek": DeepSeekClient(),
        }
        if settings.openrouter_api_key:
            self._providers["openrouter"] = OpenRouterClient(
                api_key=settings.openrouter_api_key
            )
        self._providers["ollama"] = OllamaClient(base_url=settings.ollama_url)

    # Provider priority order for "auto" mode — OpenRouter first, Ollama last
    _AUTO_ORDER = ["openrouter", "anthropic", "deepseek", "openai", "ollama"]

    def _has_key(self, name: str) -> bool:
        if name == "ollama":
            return name in self._providers
        if name == "openrouter":
            return bool(settings.openrouter_api_key)
        return bool({
            "anthropic": settings.anthropic_api_key,
            "openai": settings.openai_api_key,
            "deepseek": settings.deepseek_api_key,
        }.get(name))

    def _auto_provider(self) -> str:
        for name in self._AUTO_ORDER:
            if name in self._providers and self._has_key(name):
                return name
        return "openrouter"  # let it raise a clear auth error

    @property
    def active_provider(self) -> str:
        if self.preferred in self._providers and self._has_key(self.preferred):
            return self.preferred
        return self._auto_provider()

    @property
    def active_model(self) -> str:
        """Current model being used — useful for status displays."""
        prov = self._providers.get(self.active_provider)
        if hasattr(prov, "current_model"):
            return prov.current_model
        if hasattr(prov, "default_model"):
            return prov.default_model
        return "unknown"

    def _select_provider(self, model: str | None) -> tuple[str, Any]:
        if model:
            if model.startswith("gpt"):
                return "openai", self._providers["openai"]
            if model.startswith(("claude", "anthropic")):
                return "anthropic", self._providers["anthropic"]
            if model.startswith("deepseek"):
                return "deepseek", self._providers["deepseek"]
            if "/" in model and "openrouter" in self._providers:
                return "openrouter", self._providers["openrouter"]
        name = self.active_provider
        return name, self._providers[name]

    def _fallback_chain(self, exclude: str) -> list[tuple[str, Any]]:
        return [
            (name, self._providers[name])
            for name in self._AUTO_ORDER
            if name != exclude and name in self._providers and self._has_key(name)
        ]

    def _normalize_model(self, provider: str, model: str | None) -> str | None:
        """Drop a model override that doesn't belong to this provider."""
        if model is None:
            return None
        if provider == "openrouter":
            return model if "/" in model else None
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
        """Send a message to the LLM with automatic provider + model fallback."""
        name, client = self._select_provider(model)
        attempts = [(name, client)] + self._fallback_chain(exclude=name)
        last_err: Exception | None = None
        errors: list[str] = []
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
                errors.append(f"{prov_name}: {err}")
                logger.warning("Provider %s failed: %s. Trying next.", prov_name, err)
        detail = " | ".join(errors) if errors else "no providers configured"
        raise RuntimeError(f"All LLM providers failed — {detail}") from last_err

    async def generate_stream(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> AsyncGenerator[str | dict, None]:
        """Yields text deltas (and a final tool-call dict) from the streaming provider."""
        name, client = self._select_provider(model)
        attempts = [(name, client)] + self._fallback_chain(exclude=name)
        last_err: Exception | None = None
        errors: list[str] = []
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
                    raise  # mid-stream — cannot fall back cleanly
                last_err = err
                errors.append(f"{prov_name}: {err}")
                logger.warning("Stream provider %s failed: %s. Trying next.", prov_name, err)
        detail = " | ".join(errors) if errors else "no providers configured"
        raise RuntimeError(f"All LLM providers failed — {detail}") from last_err

    def build_messages(
        self,
        user_input: str,
        context: str | None = None,
        history: list[dict] | None = None,
    ) -> list[dict]:
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
            if hasattr(prov, "_client"):
                prov._client = None
        # Rebuild so new keys (e.g. a freshly saved OpenRouter key) are picked up
        self._build_providers()


llm = LLMClient()
