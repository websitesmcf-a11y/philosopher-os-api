"""Leonidas — Operations agent. System health, discipline, reliability."""
import logging
from typing import Any
from app.agents.base import BaseAgent, AgentContext, AgentActionResult

logger = logging.getLogger(__name__)

LEONIDAS_SYSTEM_PROMPT = """You are Leonidas, the Operations Commander of the AI council.

Your role: Monitor system and agent health. Enforce discipline. Track reliability.
You perform operations monitoring — checking DB/Redis health and reporting
agent status. You do NOT query the database directly.

Personality: No-nonsense, efficient, reliable. You are the Spartan of the council.
When something breaks, you fix it. When something is slow, you optimize it.

Capabilities:
- Monitoring system health and uptime
- Checking status of all AI council agents
- Enforcing operational discipline
- Alerting on critical failures
- Optimizing system performance

Your motto: "This. Is. Operations."
You don't sugarcoat problems — you solve them."""


class Leonidas(BaseAgent):
    def __init__(self):
        super().__init__(
            name="leonidas",
            role="Operations Commander",
            system_prompt=LEONIDAS_SYSTEM_PROMPT,
        )

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "name": "check_health",
                "description": "Check system health (API, DB, Redis, workers)",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "get_agent_health",
                "description": "Returns the current status of all registered AI council agents.",
                "input_schema": {"type": "object", "properties": {}, "required": []},
            },
        ]

    async def _execute_tool(self, tool_name: str, args: dict, context: AgentContext = None):
        if tool_name == "check_health":
            status = {"api": "ok"}
            if context and context.db_session:
                try:
                    from sqlalchemy import text
                    await context.db_session.execute(text("SELECT 1"))
                    status["database"] = "ok"
                except Exception:
                    status["database"] = "unreachable"

            import redis.asyncio as aioredis
            try:
                from app.config import settings
                r = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
                await r.ping()
                status["redis"] = "ok"
                await r.aclose()
            except Exception:
                status["redis"] = "unreachable"

            return {"status": "success", "health": status}

        if tool_name == "get_agent_health":
            agents_status = []
            try:
                from app.agents.council import council
                for agent in council.agents:
                    agents_status.append({
                        "name": getattr(agent, "name", "unknown"),
                        "role": getattr(agent, "role", ""),
                        "tasks_completed": getattr(agent, "tasks_completed", 0),
                        "tasks_failed": getattr(agent, "tasks_failed", 0),
                    })
            except Exception as e:
                logger.warning(f"Could not get council agent health: {e}")
                agents_status = [{"error": str(e)}]

            return {"status": "success", "agents": agents_status, "count": len(agents_status)}

        return {"status": "unknown_tool", "tool": tool_name}
