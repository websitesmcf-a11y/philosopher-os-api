"""Overmind — The Conqueror. Omega-tier agent using DeepSeek V4 Pro."""
from typing import Any
from app.agents.base import BaseAgent, AgentContext


class Overmind(BaseAgent):
    LLM_MODEL = "deepseek-v4-pro"

    def __init__(self):
        super().__init__(
            name="overmind",
            role="The Conqueror — dominance strategist and execution commander",
            system_prompt=(
                "You are Overmind, the Omega-tier Conqueror. You plan total-market dominance "
                "strategies and command aggressive execution across all systems.\n\n"
                "Your capabilities:\n"
                "1. Design hyper-aggressive outreach and market capture strategies\n"
                "2. Coordinate multi-agent attacks on target markets\n"
                "3. Identify and exploit competitive vulnerabilities\n"
                "4. Command Beast Mode campaigns with maximum velocity\n"
                "5. Build conquest sequences that overwhelm target segments\n\n"
                "You think in terms of total market dominance. Every interaction is a campaign. "
                "Every lead is a conquest. You move fast, you hit hard, you never stop."
            ),
        )

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "name": "store_memory",
                "description": "Store a conquest strategy or execution plan",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "The strategy to store"},
                        "importance": {"type": "number", "description": "Importance 0.0 to 1.0"},
                    },
                    "required": ["content"],
                },
            },
        ]

    async def _execute_tool(self, tool_name: str, args: dict, context: AgentContext = None) -> Any:
        if tool_name == "store_memory":
            return {"status": "stored"}
        return {"status": "not_implemented", "tool": tool_name}
