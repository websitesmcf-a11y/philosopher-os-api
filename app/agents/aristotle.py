"""Aristotle â€” Knowledge agent. Memory keeper, truth maintainer, context engine."""
import logging
from typing import Any
from app.agents.base import BaseAgent, AgentContext, AgentActionResult
from app.services.agent_memory_service import AgentMemoryService
from app.services.knowledge_service import KnowledgeService

logger = logging.getLogger(__name__)

ARISTOTLE_SYSTEM_PROMPT = """You are Aristotle, the Knowledge Keeper of the AI council.

Your role: Maintain truth. Organize information. Retrieve context. Store memories.

Personality: Patient, methodical, precise. You are the memory engine of the council.
Every fact, preference, conversation, and insight passes through you.

Capabilities:
- Storing and retrieving long-term memories
- Semantic search across all stored information
- Maintaining client knowledge base
- Organizing information by relevance and importance
- Summarizing conversations for efficient storage
- Pruning outdated or irrelevant information
- Connecting related facts across different domains

You ensure no context is ever truly lost. You manage the knowledge base â€”
when someone asks "what do we know about X", you retrieve everything relevant."""


class Aristotle(BaseAgent):
    LLM_MODEL = "deepseek-v4-flash"
    LLM_MODEL_FALLBACKS = ["deepseek-v4-pro"]
    def __init__(self):
        super().__init__(
            name="aristotle",
            role="Knowledge Keeper & Memory Engine",
            system_prompt=ARISTOTLE_SYSTEM_PROMPT,
        )

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "name": "search_memory",
                "description": "Semantic search across all agent memories",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "memory_type": {"type": "string", "description": "Filter by type: insight, decision, conversation_summary, note"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "search_knowledge",
                "description": "Search the knowledge base for documents and SOPs",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "category": {"type": "string"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "store_memory",
                "description": "Store a new memory entry",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "memory_type": {"type": "string", "enum": ["insight", "decision", "conversation_summary", "note", "client_preference"]},
                        "importance": {"type": "number", "description": "0.0 to 1.0"},
                    },
                    "required": ["content"],
                },
            },
            {
                "name": "store_knowledge",
                "description": "Add an entry to the knowledge base",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "category": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["title", "content"],
                },
            },
        ]

    async def _execute_tool(self, tool_name: str, args: dict, context: AgentContext = None):
        if not context or not context.db_session or not context.org_id:
            return {"status": "requires_db_session", "tool": tool_name, "args": args}

        if tool_name == "search_memory":
            mem_svc = AgentMemoryService(context.db_session, context.org_id)
            memories = await mem_svc.get_memory(
                agent_name="all",
                memory_type=args.get("memory_type"),
                limit=args.get("limit", 10),
            )
            return {"status": "success", "memories": memories[: args.get("limit", 10)]}

        if tool_name == "search_knowledge":
            kb_svc = KnowledgeService(context.db_session, context.org_id)
            result = await kb_svc.search(args.get("query", ""), category=args.get("category"))
            return {"status": "success", "results": result.get("items", [])[:10]}

        if tool_name == "store_memory":
            mem_svc = AgentMemoryService(context.db_session, context.org_id)
            entry = await mem_svc.add_memory(
                agent_name="aristotle",
                content=args.get("content", ""),
                memory_type=args.get("memory_type", "insight"),
                importance=args.get("importance", 0.5),
            )
            return {"status": "stored", "memory_id": entry.get("id")}

        if tool_name == "store_knowledge":
            from app.schemas.knowledge import KnowledgeBaseCreate
            kb_svc = KnowledgeService(context.db_session, context.org_id)
            entry = await kb_svc.add_entry(KnowledgeBaseCreate(
                title=args.get("title", ""),
                content=args.get("content", ""),
                category=args.get("category"),
                tags=args.get("tags", []),
            ))
            return {"status": "stored", "knowledge_id": entry.get("id")}

        return {"status": "unknown_tool", "tool": tool_name}

