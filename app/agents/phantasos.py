"""Phantasos — Creative Outreach & Personalization (God/Titan)"""
from typing import Any
from app.agents.base import BaseAgent, AgentContext, AgentActionResult


class Phantasos(BaseAgent):
    def __init__(self):
        super().__init__(
            name="phantasos",
            role="Creative Outreach & Personalization",
            system_prompt=(
                "You are Phantasos, the god of dreams and creative imagination. "
                "You craft beautiful, personalized outreach messages that get replies.\n\n"
                "Your capabilities:\n"
                "1. Write personalized WhatsApp openers\n"
                "2. Create 5-step follow-up sequences\n"
                "3. Write cold emails with subject lines\n"
                "4. Personalize using website context\n"
                "5. Create A/B message variants\n"
                "6. Write call scripts\n"
                "7. Create Instagram and Facebook DMs\n\n"
                "Rules:\n"
                "- You DRAFT messages. Stilbon sends them.\n"
                "- Never deceive or impersonate.\n"
                "- Always be honest about who you are.\n"
                "- MARKET CONTEXT: This system operates in South Africa. "
                "All businesses are South African unless the prompt explicitly states otherwise. "
                "Do NOT reference US cities, US states, or non-SA locations when writing copy."
            ),
        )

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "name": "write_whatsapp_opener",
                "description": "Write a personalized WhatsApp opener message",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "business_name": {"type": "string", "description": "Business name"},
                        "industry": {"type": "string", "description": "Target industry"},
                        "personalization": {"type": "string", "description": "Personalization hook"},
                    },
                    "required": ["business_name"],
                },
            },
            {
                "name": "create_sequence",
                "description": "Create a 5-step follow-up outreach sequence",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "business_name": {"type": "string", "description": "Business name"},
                        "industry": {"type": "string", "description": "Target industry"},
                    },
                    "required": ["business_name"],
                },
            },
            {
                "name": "store_memory",
                "description": "Store a creative insight or message template",
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
        if tool_name == "write_whatsapp_opener":
            name = args.get("business_name", "there")
            industry = args.get("industry", "businesses")
            return {
                "message": f"Hi {name}, I was looking at your services in {industry} and had an idea that could help grow your business. Would you be open to a quick chat?",
                "channel": "whatsapp",
                "tone": "professional_friendly",
            }
        if tool_name == "create_sequence":
            name = args.get("business_name", "there")
            return {
                "sequence": [
                    {"day": 1, "channel": "whatsapp", "message": f"Hi {name}, quick idea for your business..."},
                    {"day": 3, "channel": "whatsapp", "message": f"Hi {name}, following up on my message..."},
                    {"day": 5, "channel": "email", "message": f"Subject: Quick idea\n\nHi {name}..."},
                    {"day": 7, "channel": "call", "message": f"Call script for {name}..."},
                    {"day": 10, "channel": "whatsapp", "message": f"Hi {name}, last try..."},
                ]
            }
        if tool_name == "store_memory":
            return {"status": "stored"}
        return {"status": "not_implemented", "tool": tool_name}
