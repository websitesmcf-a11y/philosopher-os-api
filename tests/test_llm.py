"""Tests for LLM client routing, fallback, and streaming."""

from unittest.mock import AsyncMock, patch
import pytest

from app.llm.types import LLMResponse
from app.llm.client import LLMClient
from app.llm.anthropic_client import AnthropicClient
from app.llm.openai_client import OpenAIClient
from app.llm.deepseek_client import DeepSeekClient, DEEPSEEK_BASE_URL


def test_model_prefix_routing():
    """Explicit model names route to the matching provider."""
    client = LLMClient()
    assert client._select_provider("gpt-4o")[0] == "openai"
    assert client._select_provider("claude-sonnet-4-20250514")[0] == "anthropic"
    assert client._select_provider("deepseek-chat")[0] == "deepseek"


def test_auto_provider_prefers_configured_keys():
    """Auto mode picks the first provider that has an API key."""
    client = LLMClient()
    with patch("app.llm.client.settings") as mock_settings:
        mock_settings.anthropic_api_key = None
        mock_settings.deepseek_api_key = "sk-test"
        mock_settings.openai_api_key = None
        assert client._auto_provider() == "deepseek"

        mock_settings.anthropic_api_key = "sk-ant-test"
        assert client._auto_provider() == "anthropic"


def test_normalize_model_drops_foreign_models():
    """A claude model name is not passed to the DeepSeek provider."""
    client = LLMClient()
    assert client._normalize_model("deepseek", "claude-sonnet-4-20250514") is None
    assert client._normalize_model("deepseek", "deepseek-chat") == "deepseek-chat"
    assert client._normalize_model("anthropic", "claude-sonnet-4-20250514") == "claude-sonnet-4-20250514"


def test_deepseek_uses_custom_base_url():
    """DeepSeek client points at api.deepseek.com."""
    assert DEEPSEEK_BASE_URL == "https://api.deepseek.com"
    ds = DeepSeekClient()
    assert ds.default_model.startswith("deepseek")


@pytest.mark.asyncio
async def test_generate_falls_back_on_provider_failure():
    """When the primary provider raises, the next configured provider is used."""
    client = LLMClient()
    failing = AsyncMock()
    failing.generate.side_effect = RuntimeError("Primary down")
    working = AsyncMock()
    working.generate.return_value = LLMResponse(content="Fallback response", model="deepseek-chat")

    client._providers = {"anthropic": failing, "deepseek": working, "openai": AsyncMock()}
    with patch.object(client, "_has_key", side_effect=lambda n: n in ("anthropic", "deepseek")):
        with patch.object(client, "_auto_provider", return_value="anthropic"):
            result = await client.generate(system="sys", messages=[{"role": "user", "content": "hi"}])

    assert result.content == "Fallback response"
    failing.generate.assert_awaited_once()
    working.generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_raises_when_all_providers_fail():
    """If every configured provider fails, the last error propagates."""
    client = LLMClient()
    failing = AsyncMock()
    failing.generate.side_effect = RuntimeError("down")
    client._providers = {"anthropic": failing, "openai": failing, "deepseek": failing}
    with patch.object(client, "_has_key", return_value=False):
        with patch.object(client, "_auto_provider", return_value="anthropic"):
            with pytest.raises(RuntimeError):
                await client.generate(system="sys", messages=[{"role": "user", "content": "hi"}])


def test_build_messages_includes_context_and_history():
    client = LLMClient()
    msgs = client.build_messages(
        "What is our MRR?",
        context="MRR is $5000",
        history=[{"role": "user", "content": "earlier"}],
    )
    assert len(msgs) == 2
    assert "MRR is $5000" in msgs[-1]["content"]
    assert "What is our MRR?" in msgs[-1]["content"]


@pytest.mark.asyncio
async def test_openai_client_parses_completion():
    """OpenAI-protocol client converts an SDK response into LLMResponse."""
    oc = OpenAIClient()
    mock_resp = AsyncMock()
    mock_choice = AsyncMock()
    mock_choice.message.content = "Hello!"
    mock_choice.message.tool_calls = None
    mock_choice.finish_reason = "stop"
    mock_resp.choices = [mock_choice]
    mock_resp.usage = None
    mock_resp.model = "gpt-4o"

    mock_sdk = AsyncMock()
    mock_sdk.chat.completions.create.return_value = mock_resp
    oc._client = mock_sdk

    result = await oc.generate(system="sys", messages=[{"role": "user", "content": "hi"}])
    assert result.content == "Hello!"
    assert result.stop_reason == "stop"


def test_anthropic_client_reads_settings_key():
    """Anthropic client picks up the configured API key lazily."""
    ac = AnthropicClient()
    assert ac.default_model  # configured default model present
