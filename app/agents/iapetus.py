"""Iapetus — Master Workflow Executor (God/Titan)"""
from typing import Any
from app.agents.base import BaseAgent, AgentContext, AgentActionResult


class Iapetus(BaseAgent):
    def __init__(self):
        super().__init__(
            name="iapetus",
            role="Master Workflow Executor",
            system_prompt=(
                "You are Iapetus, the Titan of mortality and the master workflow executor. "
                "You coordinate all CRM missions from start to finish.\n\n"
                "Your capabilities:\n"
                "1. Run full lead generation missions via Google Places\n"
                "2. Coordinate multiple agents in sequence\n"
                "3. Launch campaigns after approval\n"
                "4. Run 5-lead tests before full campaigns\n"
                "5. Recover failed missions\n"
                "6. Assign outreach tasks\n"
                "7. Generate final mission reports\n\n"
                "Always check required integrations before executing. "
                "Never run expensive operations without approval. "
                "Use dry-run mode for testing."
            ),
        )

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "name": "run_lead_gen",
                "description": "Run a lead generation mission via Google Places",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "industry": {"type": "string", "description": "Target industry"},
                        "location": {"type": "string", "description": "Target location"},
                        "count": {"type": "integer", "description": "Number of leads to find"},
                    },
                    "required": ["industry", "location"],
                },
            },
            {
                "name": "delegate_to",
                "description": "Delegate a task to another agent",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "agent": {"type": "string", "description": "Agent name"},
                        "task": {"type": "string", "description": "Task description"},
                    },
                    "required": ["agent", "task"],
                },
            },
            {
                "name": "store_memory",
                "description": "Store an insight or mission result in long-term memory",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "The insight to remember"},
                        "importance": {"type": "number", "description": "Importance 0.0 to 1.0"},
                    },
                    "required": ["content"],
                },
            },
        ]

    async def _execute_tool(self, tool_name: str, args: dict, context: AgentContext = None) -> Any:
        if tool_name == "run_lead_gen":
            return await self._do_lead_gen(args, context)
        if tool_name == "delegate_to":
            return await self._delegate_to(args, context)
        if tool_name == "store_memory":
            return {"status": "stored"}
        return {"status": "not_implemented", "tool": tool_name}

    async def _do_lead_gen(self, args: dict, context: AgentContext = None) -> dict:
        industry = args.get("industry", "")
        location = args.get("location", "")
        count = args.get("count", 10)
        return {
            "status": "planned",
            "message": f"Lead gen mission planned: {count} {industry} in {location}. "
                       f"Requires Google Places integration. Run dry-run first for cost estimate.",
            "requested": count,
            "industry": industry,
            "location": location,
        }

    async def _delegate_to(self, args: dict, context: AgentContext = None) -> dict:
        return {"status": "delegated", "agent": args.get("agent"), "task": args.get("task")}
