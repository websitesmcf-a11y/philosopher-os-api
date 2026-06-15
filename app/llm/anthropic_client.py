"""Anthropic Claude client for the Socrates AI council."""
import logging
import json
from typing import Any, AsyncGenerator
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from app.config import settings
from app.llm.types import LLMResponse, ToolCall

logger = logging.getLogger(__name__)


class AnthropicRateLimitError(Exception):
    """Raised when Anthropic API returns a 429 rate limit error."""


class AnthropicClient:
    """Client for Anthropic Claude API with streaming and tool use support."""

    def __init__(self):
        self.api_key = settings.anthropic_api_key
        self.default_model = settings.default_llm_model
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import anthropic
            # Re-read the key so connections saved via the UI take effect
            self.api_key = settings.anthropic_api_key or self.api_key
            self._client = anthropic.AsyncAnthropic(api_key=self.api_key, timeout=60.0)
        return self._client

    def _check_rate_limit(self, exc: Exception) -> bool:
        return isinstance(exc, AnthropicRateLimitError)

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

        kwargs = dict(
            model=model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if tools:
            kwargs["tools"] = tools

        if stream:
            return await self._stream(**kwargs)
        return await self._complete(**kwargs)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(AnthropicRateLimitError),
        reraise=True,
    )
    async def _complete(self, **kwargs) -> LLMResponse:
        try:
            resp = await self.client.messages.create(**kwargs)
            tool_calls = []
            content = ""

            for block in resp.content:
                if block.type == "text":
                    content += block.text
                elif block.type == "tool_use":
                    tool_calls.append(ToolCall(
                        name=block.name,
                        arguments=block.input if isinstance(block.input, dict) else {},
                        id=block.id,
                    ))

            usage = {}
            if resp.usage:
                usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
            logger.info("Anthropic API success", extra={"model": resp.model, "usage": usage})

            return LLMResponse(
                content=content,
                tool_calls=tool_calls,
                stop_reason=resp.stop_reason or "",
                usage=usage,
                model=resp.model,
                raw=resp,
            )
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "rate_limit" in error_str.lower():
                logger.warning(f"Anthropic rate limit hit, will retry: {e}")
                raise AnthropicRateLimitError(error_str) from e
            logger.error(f"Anthropic API error: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(AnthropicRateLimitError),
        reraise=True,
    )
    async def _stream(self, **kwargs) -> LLMResponse:
        try:
            content = ""
            tool_calls: dict[str, ToolCall] = {}
            stop_reason = ""

            async with self.client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            content += event.delta.text
                    elif event.type == "content_block_start":
                        if event.content_block.type == "tool_use":
                            tc = ToolCall(
                                name=event.content_block.name,
                                arguments={},
                                id=event.content_block.id,
                            )
                            tool_calls[tc.id] = tc
                    elif event.type == "content_block_delta" and event.delta.type == "input_json_delta":
                        for tc in tool_calls.values():
                            tc.arguments = event.delta.partial_json or "{}"
                    elif event.type == "message_delta":
                        stop_reason = event.delta.stop_reason or ""

            return LLMResponse(
                content=content,
                tool_calls=list(tool_calls.values()),
                stop_reason=stop_reason,
                model=kwargs.get("model", "unknown"),
                usage={},
            )
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "rate_limit" in error_str.lower():
                logger.warning(f"Anthropic stream rate limit hit, will retry: {e}")
                raise AnthropicRateLimitError(error_str) from e
            logger.error(f"Anthropic streaming error: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(AnthropicRateLimitError),
        reraise=True,
    )
    async def generate_stream(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> AsyncGenerator[str, None]:
        """Yields text deltas as they arrive from the Anthropic API stream."""
        model = model or self.default_model

        kwargs = dict(
            model=model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if tools:
            kwargs["tools"] = tools

        try:
            async with self.client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            yield event.delta.text
                    elif event.type == "message_delta":
                        pass
                final = await stream.get_final_message()
                calls = [
                    {"id": block.id, "name": block.name, "arguments": block.input or {}}
                    for block in final.content
                    if block.type == "tool_use"
                ]
                if calls:
                    yield {"__tool_calls__": calls}
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "rate_limit" in error_str.lower():
                logger.warning(f"Anthropic generate_stream rate limit hit: {e}")
                raise AnthropicRateLimitError(error_str) from e
            logger.error(f"Anthropic stream error: {e}")
            raise
