"""Council orchestrator — routes user input to appropriate agent(s) with LLM-enhanced routing."""
import json
import logging
from typing import Any, AsyncGenerator
from app.agents.base import BaseAgent, AgentContext, AgentActionResult, ToolEvent
from app.llm.client import llm

logger = logging.getLogger(__name__)


class CouncilOrchestrator:
    """Routes requests to the correct agent(s) and synthesizes responses."""

    def __init__(self):
        self.agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent):
        self.agents[agent.name] = agent
        agent.council = self  # backref so agents can delegate to each other
        logger.info(f"Registered agent: {agent.name} ({agent.role})")

    async def route(self, user_input: str, org_id: str | None = None) -> str:
        """Determine the primary agent using keyword matching + optional LLM routing."""
        import re
        input_lower = user_input.lower()

        # Fast keyword-based routing
        if any(w in input_lower for w in ["money", "invoice", "mrr", "revenue", "budget", "cost", "profit", "finance", "payment", "billing"]):
            return "solon"
        if any(w in input_lower for w in ["analytics", "metrics", "numbers", "stats", "report", "dashboard", "forecast", "performance"]):
            return "pythagoras"
        if any(w in input_lower for w in ["critique", "strategy", "think", "evaluate", "analyze", "should i", "what if", "review", "assess"]):
            return "socrates"
        if any(w in input_lower for w in ["status", "health", "broken", "error", "down", "deploy", "system", "server", "infra"]):
            return "archimedes"
        if any(w in input_lower for w in ["schedule", "calendar", "meeting", "appointment", "book", "remind", "organize", "agenda"]):
            return "athena"

        # Stilbon (direct messaging) — checked BEFORE broad "message" / odysseus routing
        # so "message 0731234567" or "whatsapp John at +27..." goes to the right agent.
        _has_phone = bool(re.search(r'\b(\+27|0[6-8]\d)[0-9\s\-]{5,}\b', user_input))
        if _has_phone:
            return "stilbon"
        if any(w in input_lower for w in [
            "send now", "send whatsapp", "whatsapp message", "send a whatsapp",
            "stilbon", "deliver", "transmit", "broadcast", "dispatch", "batch send",
        ]):
            return "stilbon"

        # God/Titan routing
        if any(w in input_lower for w in ["lead gen", "collect leads", "bulk", "mass", "iapetus", "master workflow", "orchestrate", "full mission"]):
            return "iapetus"
        if any(w in input_lower for w in ["opportunity", "intelligence", "hot lead", "signal", "trend", "market analysis", "next best", "astraeus"]):
            return "astraeus"
        if any(w in input_lower for w in ["clean", "duplicate", "dedup", "erebos", "integrity", "audit", "quarantine", "broken data", "dirty", "cleanup", "merge"]):
            return "erebos"
        if any(w in input_lower for w in ["draft", "copy", "write", "creative", "personalize", "outreach message", "phantasos", "sequence", "follow-up"]):
            return "phantasos"

        # Odysseus handles campaign/social/outreach (not direct single sends)
        if any(w in input_lower for w in ["outreach", "campaign", "contact", "message", "whatsapp", "email", "drip", "follow", "facebook", "instagram", "linkedin", "post on", "social"]):
            return "odysseus"

        if any(w in input_lower for w in ["research", "find", "search", "look up", "investigate", "market", "industry", "competitor", "trend"]):
            return "heraclitus"

        if any(w in input_lower for w in ["remember", "knowledge", "what do we know", "memory", "find information"]):
            return "aristotle"
        if any(w in input_lower for w in ["operations", "worker", "queue", "task status", "monitor", "uptime"]):
            return "leonidas"

        # LLM-based routing fallback for ambiguous requests
        if len(user_input) > 10:
            try:
                route_prompt = f"""Given this user request, pick the single best agent from: {', '.join(self.agents.keys())}.
Request: "{user_input}"
Respond with ONLY the agent name."""
                route_response = await llm.generate(
                    system="You route user requests to the best specialist agent.",
                    messages=[{"role": "user", "content": route_prompt}],
                    temperature=0.1,
                    max_tokens=20,
                )
                candidate = route_response.content.strip().lower()
                if candidate in self.agents:
                    logger.info(f"LLM routing: {user_input[:50]}... -> {candidate}")
                    return candidate
            except Exception as e:
                logger.warning(f"LLM routing fallback failed: {e}")

        # Default to CEO
        return "plato"

    async def process(
        self,
        user_input: str,
        org_id: str | None = None,
        db_session: Any = None,
        conversation_history: list[dict] | None = None,
        agent: str | None = None,
    ) -> dict:
        """Process user input through the council with context.

        If `agent` names a registered agent, it handles the request directly
        (the user explicitly chose it); otherwise the council routes.
        """
        agent_name = agent if agent in self.agents else await self.route(user_input, org_id)
        agent = self.agents.get(agent_name)

        if not agent:
            return {
                "reply": f"Agent '{agent_name}' is not available.",
                "agent": agent_name,
                "conversation_id": None,
                "actions": [],
                "success": False,
            }

        context = AgentContext(
            user_input=user_input,
            org_id=org_id,
            db_session=db_session,
            conversation_history=conversation_history,
        )
        result = await agent.run(context)

        # The specialist's answer IS the answer. (A second Plato pass here used
        # to restate every response, which read as the agent repeating itself.)
        return {
            "reply": result.message,
            "agent": agent.name,
            "agent_role": agent.role,
            "conversation_id": None,  # callers own conversation persistence
            "actions": result.actions,
            "tool_calls": result.tool_calls,
            "data": result.data,
            "success": result.success,
        }

    async def process_stream(
        self,
        user_input: str,
        org_id: str | None = None,
        db_session: Any = None,
        conversation_history: list[dict] | None = None,
        agent: str | None = None,
        conversation_id: str | None = None,
        on_complete: Any = None,
    ) -> AsyncGenerator[str, None]:
        """Process user input through the council and yield SSE-formatted chunks.

        `on_complete(agent_name, full_text)` is awaited after the stream ends
        so callers can persist the assistant turn.
        """
        agent_name = agent if agent in self.agents else await self.route(user_input, org_id)
        agent = self.agents.get(agent_name)

        if not agent:
            yield f"data: {json.dumps({'type': 'error', 'content': f'Agent {agent_name} is not available.'})}\n\n"
            return

        context = AgentContext(
            user_input=user_input,
            org_id=org_id,
            db_session=db_session,
            conversation_history=conversation_history,
        )

        yield f"data: {json.dumps({'type': 'meta', 'agent': agent.name, 'agent_role': agent.role, 'conversation_id': conversation_id or ''})}\n\n"

        full_content = ""
        async for delta in agent.think_stream(context):
            if isinstance(delta, ToolEvent):
                yield delta.to_sse()
            elif delta:
                full_content += delta
                yield f"data: {json.dumps({'type': 'token', 'content': delta, 'agent': agent.name})}\n\n"

        if on_complete:
            await on_complete(agent.name, full_content)

        yield f"data: {json.dumps({'type': 'done', 'agent': agent.name, 'conversation_id': conversation_id or ''})}\n\n"
