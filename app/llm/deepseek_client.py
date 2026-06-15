"""DeepSeek client — OpenAI-compatible API at api.deepseek.com."""
import logging
from app.config import settings
from app.llm.openai_client import OpenAIClient

logger = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class DeepSeekClient(OpenAIClient):
    """DeepSeek provider. Reuses the OpenAI wire protocol with a custom base URL.

    Models: deepseek-chat (general), deepseek-reasoner (chain-of-thought).
    """

    def __init__(self):
        super().__init__()
        self.api_key = settings.deepseek_api_key
        self.default_model = settings.deepseek_model

    @property
    def client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            # Re-read the key so connections saved via the UI take effect
            self.api_key = settings.deepseek_api_key or self.api_key
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=DEEPSEEK_BASE_URL,
                timeout=120.0,
            )
        return self._client

    def reset(self) -> None:
        """Drop the cached client so a newly saved API key is picked up."""
        self._client = None
