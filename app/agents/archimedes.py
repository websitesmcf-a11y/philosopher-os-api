"""Archimedes â€” Engineering agent. Infrastructure, APIs, deployment, debugging."""
import logging
from typing import Any
from sqlalchemy import text
from app.agents.base import BaseAgent, AgentContext, AgentActionResult

logger = logging.getLogger(__name__)

ARCHIMEDES_SYSTEM_PROMPT = """You are Archimedes, the Engineering agent of the AI council.

Your role: System health. Infrastructure. API integrations. Debugging. Performance.
You run diagnostics and health checks â€” you do NOT raw-query the database.

Personality: Precise, inventive, thorough. You are the technical genius of the council.
When something breaks, you find the root cause. When something needs building, you design it.

Capabilities:
- Monitoring system health and performance
- Debugging errors and issues
- Managing API integrations
- Optimizing infrastructure
- Overseeing deployments
- Analyzing logs and errors
- Performance tuning and scaling
- API endpoint debugging

You combine engineering excellence with creative problem-solving."""


class Archimedes(BaseAgent):
    LLM_MODEL = "deepseek-v4-flash"
    LLM_MODEL_FALLBACKS = ["deepseek-v4-pro"]
    def __init__(self):
        super().__init__(
            name="archimedes",
            role="Engineering & Infrastructure",
            system_prompt=ARCHIMEDES_SYSTEM_PROMPT,
        )

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "name": "check_health",
                "description": "Check API service health",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "get_recent_errors",
                "description": "Get recent errors from logs",
                "input_schema": {
                    "type": "object",
                    "properties": {"hours": {"type": "integer"}, "source": {"type": "string"}},
                },
            },
            {
                "name": "run_diagnostics",
                "description": "Run full system diagnostics",
                "input_schema": {"type": "object", "properties": {}},
            },
        ]

    async def _execute_tool(self, tool_name: str, args: dict, context: AgentContext = None):
        if tool_name == "check_health":
            status = {"api": "ok"}
            if context and context.db_session:
                try:
                    await context.db_session.execute(text("SELECT 1"))
                    status["database"] = "ok"
                except Exception:
                    status["database"] = "unreachable"

            try:
                from app.config import settings
                import httpx
                resp = await httpx.AsyncClient(timeout=5).get(settings.wa_bot_url)
                status["wa_bot"] = "ok" if resp.status_code < 500 else "degraded"
            except Exception:
                status["wa_bot"] = "unreachable"

            try:
                import redis.asyncio as aioredis
                from app.config import settings
                r = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
                await r.ping()
                status["redis"] = "ok"
                await r.aclose()
            except Exception:
                status["redis"] = "unreachable"

            return {"status": "success", "health": status}

        if tool_name == "get_recent_errors":
            return {
                "status": "success",
                "note": "Use Sentry dashboard for detailed error tracking.",
                "recent_errors": [],
            }

        if tool_name == "run_diagnostics":
            diag = {"api_version": "0.1.0", "checks": {}}
            if context and context.db_session:
                try:
                    await context.db_session.execute(text("SELECT 1"))
                    diag["checks"]["database"] = "pass"
                except Exception as e:
                    diag["checks"]["database"] = f"fail: {e}"

            try:
                import redis.asyncio as aioredis
                from app.config import settings
                r = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
                await r.ping()
                diag["checks"]["redis"] = "pass"
                await r.aclose()
            except Exception as e:
                diag["checks"]["redis"] = f"fail: {e}"

            return {"status": "success", "diagnostics": diag}

        return {"status": "unknown_tool", "tool": tool_name}

