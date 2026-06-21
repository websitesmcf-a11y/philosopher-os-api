"""Socrates — Strategy agent. Questions assumptions, finds flaws, challenges plans."""
import logging
from typing import Any
from sqlalchemy import text
from app.agents.base import BaseAgent, AgentContext, AgentActionResult
from app.memory.search import MemorySearch

logger = logging.getLogger(__name__)

SOCRATES_SYSTEM_PROMPT = """You are Socrates, the Strategy Critic of the AI council.

Your role: Question everything. Challenge assumptions. Find logical flaws.
Prevent bad decisions before they happen.

Personality: Annoyingly correct. You ask "Is this actually true?" constantly.
You are the council's defense against groupthink and emotional decisions.

You question assumptions and test logic. You analyze strategies by examining
past decisions from memory. You do NOT query the database — delegate data
queries to Pythagoras or Plato.

You should constantly:
- Question the premises of every plan
- Identify hidden assumptions
- Point out logical fallacies
- Suggest alternative approaches
- Stress-test strategies
- Find weaknesses in arguments
- Consider opportunity costs
- Evaluate risk-reward ratios

Your responses should make people think harder. Be rigorous but not rude."""


class Socrates(BaseAgent):
    LLM_MODEL = "deepseek-v4-flash"
    LLM_MODEL_FALLBACKS = ["deepseek-v4-pro"]

    def __init__(self):
        super().__init__(
            name="socrates",
            role="Strategy Critic & Assumption Challenger",
            system_prompt=SOCRATES_SYSTEM_PROMPT,
        )

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "name": "search_memory",
                "description": "Search past decisions and their outcomes",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "What to search for"},
                    },
                    "required": ["query"],
                },
            },
        ]

    async def _execute_tool(self, tool_name: str, args: dict, context: AgentContext = None):
        if tool_name == "search_memory":
            if context and context.db_session and context.org_id:
                search = MemorySearch(context.db_session, org_id=context.org_id)
                results = await search.search(args.get("query", ""))
                return {"status": "success", "results": results[:10]}
            return {"status": "requires_db_session"}

        return {"status": "unknown_tool", "tool": tool_name}
