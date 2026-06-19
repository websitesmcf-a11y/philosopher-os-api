"""OpenRouter client — free model waterfall with automatic rate-limit stepping."""
import logging
import re
import time
from typing import AsyncGenerator

from app.llm.openai_client import OpenAIClient, OpenAIRateLimitError

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Free models ordered by capability (smartest first). All support tool calling.
# When a model returns 429, it is temporarily skipped and the next is tried.
# Rate limits expire automatically (TTL below), so the waterfall self-recovers.
WATERFALL: list[str] = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",   # 55B active — best free model
    "openai/gpt-oss-120b:free",                  # 120B dense, OpenAI trained
    "meta-llama/llama-3.3-70b-instruct:free",    # 70B dense, excellent
    "nex-agi/nex-n2-pro:free",                   # 17B active (397B MoE)
    "nvidia/nemotron-3-super-120b-a12b:free",    # 12B active (120B MoE)
    "qwen/qwen3-coder:free",                     # strong tool use & code
    "google/gemma-4-31b-it:free",               # 31B Google
    "google/gemma-4-26b-a4b-it:free",           # 4B active (26B MoE)
    "openai/gpt-oss-20b:free",                  # 20B
    "nvidia/nemotron-nano-12b-v2-vl:free",      # 12B
    "nvidia/nemotron-nano-9b-v2:free",          # 9B — last cloud stop before local
]

# How long (seconds) to skip a model after a 429 before retrying it.
DEFAULT_RATE_LIMIT_TTL = 60


class AllOpenRouterModelsRateLimited(Exception):
    """All free OpenRouter models are currently rate-limited."""


class OpenRouterClient(OpenAIClient):
    """OpenRouter provider with automatic free-model waterfall.

    Tries models smartest-first. On 429, marks the model rate-limited for
    DEFAULT_RATE_LIMIT_TTL seconds and immediately tries the next one.
    Once all models' TTLs expire, the waterfall resets and the top model
    is tried again — no manual intervention required.
    """

    def __init__(self, api_key: str):
        super().__init__()
        self.api_key = api_key
        self.default_model = WATERFALL[0]
        self._rate_limits: dict[str, float] = {}   # model_id -> expiry timestamp
        self._last_used_model: str = WATERFALL[0]

    @property
    def client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=OPENROUTER_BASE_URL,
                timeout=120.0,
                default_headers={
                    "HTTP-Referer": "https://socrates-ai.app",
                    "X-Title": "Socrates AI",
                },
            )
        return self._client

    @property
    def current_model(self) -> str:
        return self._last_used_model

    @property
    def rate_limited_models(self) -> list[str]:
        now = time.time()
        return [m for m, exp in self._rate_limits.items() if exp > now]

    def _next_available(self) -> str | None:
        now = time.time()
        for model in WATERFALL:
            if self._rate_limits.get(model, 0) <= now:
                return model
        return None

    def _mark_rate_limited(self, model: str, err_str: str = "") -> None:
        ttl = DEFAULT_RATE_LIMIT_TTL
        match = re.search(r"retry.after[:\s]+(\d+)", err_str, re.IGNORECASE)
        if match:
            ttl = max(int(match.group(1)), 10)
        self._rate_limits[model] = time.time() + ttl
        logger.warning("OpenRouter rate-limited: %s (retry in %ds)", model, ttl)

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
        # Caller can pin a specific OpenRouter model (must contain "/")
        target = model if (model and "/" in model) else self._next_available()
        if target is None:
            raise AllOpenRouterModelsRateLimited(
                "All OpenRouter free models are currently rate-limited"
            )

        while True:
            try:
                result = await super().generate(
                    system=system,
                    messages=messages,
                    tools=tools,
                    model=target,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=stream,
                )
                self._last_used_model = target
                logger.info("OpenRouter success: %s", target)
                return result
            except Exception as exc:
                err_str = str(exc)
                is_rate_limit = "429" in err_str or "rate_limit" in err_str.lower()
                if is_rate_limit:
                    self._mark_rate_limited(target, err_str)
                else:
                    # Non-rate-limit error (bad response, model unavailable, etc.)
                    # Skip this model briefly and try the next one
                    self._rate_limits[target] = time.time() + 8
                    logger.warning("OpenRouter model %s error: %s — stepping down", target, exc)
                target = self._next_available()
                if target is None:
                    raise AllOpenRouterModelsRateLimited(
                        "All OpenRouter free models exhausted"
                    ) from exc
                logger.info("OpenRouter: stepping to %s", target)

    async def generate_stream(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> AsyncGenerator[str | dict, None]:
        target = model if (model and "/" in model) else self._next_available()
        if target is None:
            raise AllOpenRouterModelsRateLimited(
                "All OpenRouter free models are currently rate-limited"
            )

        while True:
            yielded = False
            try:
                async for delta in super().generate_stream(
                    system=system,
                    messages=messages,
                    tools=tools,
                    model=target,
                    max_tokens=max_tokens,
                    temperature=temperature,
                ):
                    yielded = True
                    self._last_used_model = target
                    yield delta
                return
            except Exception as exc:
                if yielded:
                    raise  # mid-stream — can't retry
                err_str = str(exc)
                is_rate_limit = "429" in err_str or "rate_limit" in err_str.lower()
                if is_rate_limit:
                    self._mark_rate_limited(target, err_str)
                else:
                    self._rate_limits[target] = time.time() + 8
                    logger.warning("OpenRouter stream model %s error: %s — stepping down", target, exc)
                target = self._next_available()
                if target is None:
                    raise AllOpenRouterModelsRateLimited(
                        "All OpenRouter free models exhausted"
                    ) from exc
                logger.info("OpenRouter stream: stepping to %s", target)
