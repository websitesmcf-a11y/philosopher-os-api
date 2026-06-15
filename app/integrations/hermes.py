"""Hermes Agent adapter — autonomous execution for complex workflows."""
import logging
import httpx
from typing import Any
from app.config import settings

logger = logging.getLogger(__name__)


class HermesClient:
    """Client for the Hermes autonomous agent."""

    def __init__(self):
        self.base_url = settings.hermes_api_url
        self.api_key = settings.hermes_api_key
        self._client = None

    @property
    def available(self) -> bool:
        return bool(self.base_url)

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=120.0)
        return self._client

    async def execute_task(self, task: str, context: dict | None = None) -> dict:
        """Send a task to Hermes for autonomous execution."""
        if not self.available:
            return {
                "status": "not_configured",
                "error": "Hermes API URL not set (HERMES_API_URL). Background jobs run on the in-process Hermes engine instead.",
            }
        try:
            resp = await self.client.post("/api/v1/execute", json={
                "task": task,
                "context": context or {},
            })
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Hermes execution failed: {e}")
            return {"status": "failed", "error": str(e)}

    async def get_task_status(self, task_id: str) -> dict:
        """Get the status of a previously submitted task."""
        if not self.available:
            return {"status": "not_configured", "error": "Hermes API URL not set"}
        try:
            resp = await self.client.get(f"/api/v1/tasks/{task_id}")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Hermes status check failed: {e}")
            return {"status": "unknown", "error": str(e)}

    async def research(self, query: str, depth: str = "standard") -> dict:
        """Ask Hermes to conduct autonomous research."""
        return await self.execute_task(f"Research: {query}", {"depth": depth})


hermes = HermesClient()
