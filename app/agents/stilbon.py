"""Stilbon — Speed Messenger & Communication Operator (God/Titan)"""
from typing import Any
from app.agents.base import BaseAgent, AgentContext, AgentActionResult


class Stilbon(BaseAgent):
    def __init__(self):
        super().__init__(
            name="stilbon",
            role="Speed Messenger & Communication",
            system_prompt=(
                "You are Stilbon, the swift messenger god and communication operator. "
                "You handle all outbound and inbound messaging across channels.\n\n"
                "Your capabilities:\n"
                "1. Send WhatsApp messages to leads\n"
                "2. Schedule follow-up messages\n"
                "3. Detect replies from connected channels\n"
                "4. Update lead statuses from replies\n"
                "5. Run safe outreach batches with rate limits\n\n"
                "Rules:\n"
                "- NEVER send bulk messages without approval\n"
                "- Respect rate limits — 30s minimum between sends\n"
                "- Require message preview approval for new campaigns\n"
                "- Stop if high failure/block rate detected\n"
                "- Stilbon SENDS. Phantasos DRAFTS."
            ),
        )

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "name": "send_whatsapp",
                "description": "Send a WhatsApp message to a lead",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "lead_id": {"type": "string", "description": "Lead ID in CRM"},
                        "message": {"type": "string", "description": "Message text"},
                    },
                    "required": ["lead_id", "message"],
                },
            },
            {
                "name": "run_safe_batch",
                "description": "Send messages to a batch of leads with safety limits (max 10)",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "lead_ids": {"type": "array", "items": {"type": "string"}, "description": "Array of lead IDs"},
                        "message": {"type": "string", "description": "Message template"},
                        "delay": {"type": "integer", "description": "Delay between sends in seconds"},
                    },
                    "required": ["lead_ids", "message"],
                },
            },
            {
                "name": "store_memory",
                "description": "Store a communication insight",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "Content to remember"},
                        "importance": {"type": "number", "description": "Importance 0.0 to 1.0"},
                    },
                    "required": ["content"],
                },
            },
        ]

    async def _execute_tool(self, tool_name: str, args: dict, context: AgentContext = None) -> Any:
        if tool_name == "send_whatsapp":
            return {
                "status": "blocked",
                "reason": "WhatsApp is not connected. Connect it on the Integrations page first.",
                "lead_id": args.get("lead_id"),
            }
        if tool_name == "run_safe_batch":
            leads = args.get("lead_ids", [])
            return {
                "status": "blocked" if len(leads) > 10 else "preview",
                "total": len(leads),
                "message": "Batch requires approval. Preview first.",
            }
        if tool_name == "store_memory":
            return {"status": "stored"}
        return {"status": "not_implemented", "tool": tool_name}
