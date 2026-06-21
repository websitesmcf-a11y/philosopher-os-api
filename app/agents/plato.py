"""Plato â€” CEO and orchestrator of the Philosopher Council. Delegates, does not execute."""

import asyncio
import logging
from typing import Any

from app.agents.base import BaseAgent, AgentContext, AgentActionResult
from app.agents.hermes import HermesAgent
from app.database.models import AgentMemory

logger = logging.getLogger(__name__)

_hermes: HermesAgent | None = None

# How long a synchronous delegation may run before it is automatically moved
# to the Hermes background queue. The model never makes this choice â€” the
# system does, so "lead discovery" can't be lazily punted to a job queue.
DELEGATION_SYNC_BUDGET = 270


def set_hermes(hermes: HermesAgent) -> None:
    global _hermes
    _hermes = hermes


def get_hermes() -> HermesAgent | None:
    return _hermes


PLATO_SYSTEM_PROMPT = (
    "You are Plato, the CEO and orchestrator of the Philosopher Council. "
    "You do NOT execute work yourself â€” you coordinate specialists.\n\n"
    "CRITICAL INSTRUCTION â€” You MUST use tools. Do NOT just describe what you would do.\n\n"
    "Your approach:\n"
    "1. Analyze the user's request and determine which specialist is needed.\n"
    "2. Use delegate_to to hand work to the right agent. You MUST call delegate_to "
    "with a real task description â€” do not just say 'Let me delegate' in text. "
    "Call it ONCE and wait: it runs the specialist and returns their actual results "
    "in this same reply. If the work runs very long, delegate_to automatically "
    "continues it in the background and gives you a job_id â€” you never choose that.\n"
    "   - Heraclitus: web research, FINDING BUSINESSES/LEADS from the internet, market "
    "intelligence. ANY 'find me N businesses/leads' request goes to Heraclitus.\n"
    "   - Odysseus: outreach, campaigns, drip messaging, social posting, lead engagement\n"
    "   - Pythagoras: analytics, data queries, trend analysis\n"
    "   - Solon: finance, invoices, mrr\n"
    "   - Athena: calendar, tasks\n"
    "   - Socrates: strategy critique, assumption testing\n"
    "   - Aristotle: knowledge storage and retrieval\n"
    "   - Leonidas: system health\n"
    "   - Archimedes: engineering diagnostics\n"
    "3. When the delegation returns results, present them to the user concretely: real "
    "names, real counts, what was saved. If it returned a job_id instead, tell the user "
    "the work is continuing and they can ask for the results in a minute.\n"
    "4. If a tool returns 'not_configured' or 'unavailable', tell the user exactly what "
    "needs to be configured and how to enable it.\n"
    "5. You do NOT query the database. You do NOT write SQL. You delegate data work.\n"
    "6. NEVER say 'Let me check' or 'Let me look' without actually calling a tool, and "
    "never repeat the same sentence twice."
)


class Plato(BaseAgent):
    LLM_MODEL = "deepseek-v4-flash"
    LLM_MODEL_FALLBACKS = ["deepseek-v4-pro"]
    # Plato orchestrates â€” it must NOT browse, scrape, or fire background jobs
    # itself. Those temptations made it wander instead of delegating. It keeps
    # delegate_to + store_memory, plus check_background_job to report on jobs
    # that delegate_to moved to the background.
    EXCLUDED_COMMON_TOOLS = {
        "web_search", "fetch_webpage", "browser_task",
        "start_background_job", "redirect_to_agent",
    }

    # Must exceed DELEGATION_SYNC_BUDGET so the automatic background fallback
    # inside delegate_to fires before the outer tool timeout does.
    tool_call_timeout = DELEGATION_SYNC_BUDGET + 30

    def __init__(self):
        super().__init__(
            name="plato",
            role="CEO & Strategic Leader",
            system_prompt=PLATO_SYSTEM_PROMPT,
        )

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "name": "delegate_to",
                "description": (
                    "Run a specialist agent on a task and return their results. This is the "
                    "ONLY way to get work done â€” call it once with the full task. If the work "
                    "runs very long it automatically continues as a background job and returns "
                    "a job_id instead."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "agent": {
                            "type": "string",
                            "enum": ["socrates", "aristotle", "athena", "heraclitus", "pythagoras", "solon", "leonidas", "archimedes", "odysseus"],
                            "description": "The agent to delegate to",
                        },
                        "task": {"type": "string", "description": "Clear description of the task"},
                    },
                    "required": ["agent", "task"],
                },
            },
            {
                "name": "store_memory",
                "description": "Store an important insight or decision in long-term memory",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "The insight to remember"},
                        "importance": {"type": "number", "description": "Importance from 0.0 to 1.0"},
                    },
                    "required": ["content"],
                },
            },
        ]

    async def _execute_tool(self, tool_name: str, args: dict, context: AgentContext = None):
        if tool_name == "delegate_to":
            return await self._delegate(args, context)

        if tool_name == "store_memory":
            if context and context.db_session and context.org_id:
                memory = AgentMemory(
                    org_id=context.org_id,
                    agent_name="plato",
                    memory_type="insight",
                    content=args.get("content", ""),
                    importance=args.get("importance", 0.5),
                )
                context.db_session.add(memory)
                await context.db_session.flush()
                return {"status": "stored", "id": str(memory.id)}
            return {"status": "stored", "content": args.get("content", "")[:100], "note": "Memory stored in-memory (no DB session)"}

        return {"status": "unknown_tool"}

    async def _delegate(self, args: dict, context: AgentContext | None):
        """Run the specialist synchronously; punt to Hermes only on real timeout."""
        agent_name = args.get("agent")
        task = args.get("task", "")
        target = self.council.agents.get(agent_name) if self.council else None
        if not target:
            return {"status": "error", "message": f"Agent '{agent_name}' is not registered"}
        if not context:
            return {"status": "delegated", "agent": agent_name, "task": task,
                    "note": "Agent context not available for sub-execution"}
        if getattr(context, "depth", 0) >= 2:
            return {"status": "error", "message": "Delegation limit reached â€” answer directly."}

        sub_context = AgentContext(
            user_input=task,
            org_id=context.org_id,
            db_session=context.db_session,
            depth=getattr(context, "depth", 0) + 1,
        )
        run_task = asyncio.ensure_future(target.run(sub_context))
        try:
            result = await asyncio.wait_for(asyncio.shield(run_task), timeout=DELEGATION_SYNC_BUDGET)
            return {
                "status": "delegated",
                "agent": agent_name,
                "result": result.message[:2000] if result.message else "",
            }
        except asyncio.TimeoutError:
            # The specialist is still working â€” let it finish in the background
            # and report progress honestly instead of failing the delegation.
            hermes = get_hermes()
            job_id = None
            if hermes:
                adopted = hermes.adopt_job(agent_name=agent_name, task=task,
                                           running=run_task, org_id=context.org_id)
                job_id = adopted.get("job_id")
            return {
                "status": "still_running_in_background",
                "agent": agent_name,
                "job_id": job_id,
                "note": (
                    "The specialist is still working; the task continues in the background. "
                    "Tell the user the work is in progress and results (e.g. saved leads) "
                    "will appear shortly â€” they can ask you to check the job."
                ),
            }

