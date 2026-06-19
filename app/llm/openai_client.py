"""OpenAI client for the Socrates AI council (fallback provider)."""
import logging
import json
from typing import Any, AsyncGenerator
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from app.config import settings
from app.llm.types import LLMResponse, ToolCall

logger = logging.getLogger(__name__)


class OpenAIRateLimitError(Exception):
    """Raised when OpenAI API returns a 429 rate limit error."""


class OpenAIClient:
    """Client for OpenAI API."""

    def __init__(self):
        self.api_key = settings.openai_api_key
        self.default_model = "gpt-4o"
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            # Re-read the key so connections saved via the UI take effect
            self.api_key = settings.openai_api_key or self.api_key
            self._client = AsyncOpenAI(api_key=self.api_key, timeout=60.0)
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
    ) -> LLMResponse:
        model = model or self.default_model

        openai_messages = [{"role": "system", "content": system}] + messages
        openai_tools = None
        if tools:
            openai_tools = []
            for t in tools:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", t.get("parameters", {})),
                    },
                })

        try:
            resp = await self.client.chat.completions.create(
                model=model,
                messages=openai_messages,
                tools=openai_tools or None,
                max_tokens=max_tokens,
                temperature=temperature,
                # Suppress the degenerate "Let me search... Let me check..."
                # repetition loops these models fall into with large toolsets.
                frequency_penalty=0.4,
                presence_penalty=0.3,
                stream=stream,
            )

            if stream:
                return await self._handle_stream(resp)

            if not resp.choices:
                raise ValueError(f"Model returned empty choices (model={model})")

            tool_calls = []
            content = resp.choices[0].message.content or ""

            if resp.choices[0].message.tool_calls:
                for tc in resp.choices[0].message.tool_calls:
                    tool_calls.append(ToolCall(
                        name=tc.function.name,
                        arguments=json.loads(tc.function.arguments) if tc.function.arguments else {},
                        id=tc.id,
                    ))

            usage = dict(resp.usage) if resp.usage else {}
            logger.info("OpenAI API success", extra={"model": resp.model, "usage": usage})

            return LLMResponse(
                content=content,
                tool_calls=tool_calls,
                stop_reason=resp.choices[0].finish_reason or "",
                usage=usage,
                model=resp.model,
                raw=resp,
            )
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "rate_limit" in error_str.lower():
                logger.warning(f"OpenAI rate limit hit: {e}")
                raise OpenAIRateLimitError(error_str) from e
            logger.error(f"OpenAI API error: {e}")
            raise

    async def _handle_stream(self, stream) -> LLMResponse:
        content = ""
        stop_reason = ""
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                content += delta.content
            if chunk.choices and chunk.choices[0].finish_reason:
                stop_reason = chunk.choices[0].finish_reason
        return LLMResponse(content=content, stop_reason=stop_reason)

    async def generate_stream(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> AsyncGenerator[str, None]:
        """Yields text deltas as they arrive from the OpenAI stream."""
        model = model or self.default_model

        openai_messages = [{"role": "system", "content": system}] + messages
        openai_tools = None
        if tools:
            openai_tools = []
            for t in tools:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", t.get("parameters", {})),
                    },
                })

        try:
            stream = await self.client.chat.completions.create(
                model=model,
                messages=openai_messages,
                tools=openai_tools or None,
                max_tokens=max_tokens,
                temperature=temperature,
                frequency_penalty=0.4,
                presence_penalty=0.3,
                stream=True,
                stream_options={"include_usage": False},
            )

            tool_accum: dict[int, dict] = {}
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    yield delta.content
                if delta and delta.tool_calls:
                    for tcd in delta.tool_calls:
                        ent = tool_accum.setdefault(tcd.index, {"id": "", "name": "", "args": ""})
                        if tcd.id:
                            ent["id"] = tcd.id
                        if tcd.function and tcd.function.name:
                            ent["name"] += tcd.function.name
                        if tcd.function and tcd.function.arguments:
                            ent["args"] += tcd.function.arguments

            if tool_accum:
                calls = []
                for _, ent in sorted(tool_accum.items()):
                    try:
                        args = json.loads(ent["args"]) if ent["args"] else {}
                    except json.JSONDecodeError:
                        args = {}
                    calls.append({"id": ent["id"], "name": ent["name"], "arguments": args})
                yield {"__tool_calls__": calls}
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "rate_limit" in error_str.lower():
                logger.warning(f"OpenAI generate_stream rate limit hit: {e}")
                raise OpenAIRateLimitError(error_str) from e
            logger.error(f"OpenAI stream error: {e}")
            raise
