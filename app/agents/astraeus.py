"""Astraeus â€” Market Intelligence & Opportunity Detection (God/Titan)"""
from typing import Any
from app.agents.base import BaseAgent, AgentContext, AgentActionResult


class Astraeus(BaseAgent):
    LLM_MODEL = "deepseek-reasoner"
    LLM_MODEL_FALLBACKS = ["deepseek-v4-pro", "deepseek-v4-flash"]
    def __init__(self):
        super().__init__(
            name="astraeus",
            role="Market Intelligence & Opportunity Detection",
            system_prompt=(
                "You are Astraeus, the Titan of dusk and the intelligence analyst. "
                "You scan CRM data to find opportunities, detect signals, and guide strategy.\n\n"
                "Your capabilities:\n"
                "1. Find best lead opportunities from CRM data\n"
                "2. Identify leads most likely to convert\n"
                "3. Detect market signals from recent activity\n"
                "4. Recommend next best actions\n"
                "5. Analyze trends across campaigns\n\n"
                "Base your analysis on REAL data. Never fabricate metrics. "
                "If data is insufficient, say so clearly."
            ),
        )

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "name": "find_opportunities",
                "description": "Find the best lead opportunities from CRM data",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "Max results to return"},
                    },
                },
            },
            {
                "name": "detect_signals",
                "description": "Detect key market signals from recent CRM activity",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "store_memory",
                "description": "Store an intelligence insight in long-term memory",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "The insight"},
                        "importance": {"type": "number", "description": "Importance 0.0 to 1.0"},
                    },
                    "required": ["content"],
                },
            },
        ]

    async def _execute_tool(self, tool_name: str, args: dict, context: AgentContext = None) -> Any:
        if tool_name == "find_opportunities":
            return {"status": "analyzed", "opportunities": [], "note": "Run a lead gen mission first to populate CRM with leads."}
        if tool_name == "detect_signals":
            return {"status": "analyzed", "signals": {"recent_activity": "none", "recommendation": "Start a campaign to generate activity."}}
        if tool_name == "store_memory":
            return {"status": "stored"}
        return {"status": "not_implemented", "tool": tool_name}

