"""Base agent interface with real LLM integration."""
import asyncio
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

import logging
from app.llm.client import llm as default_llm, LLMResponse
from app.memory.retrieval import ContextRetriever

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6
MAX_REDIRECT_DEPTH = 2

# Per-round streaming output cap — a single LLM turn should never spew more
# than this many characters of prose (a degenerate model can otherwise emit
# thousands of "Let me search..." lines until it hits its token budget).
MAX_ROUND_CHARS = 6000


class RepetitionGuard:
    """Detects when a streaming LLM has collapsed into a repetition loop.

    Tracks recently completed lines; trips when one short line (or the rolling
    tail) recurs past a threshold, or when total output blows past the cap.
    Used to abort a degenerate completion before it reaches the user.
    """

    def __init__(self, max_chars: int = MAX_ROUND_CHARS, line_repeat_limit: int = 4):
        self.max_chars = max_chars
        self.line_repeat_limit = line_repeat_limit
        self._buf: list[str] = []
        self._total = 0
        self._recent_lines: list[str] = []
        self._pending = ""

    def update(self, delta: str) -> bool:
        """Feed a text delta. Returns True when a repetition loop is detected."""
        self._total += len(delta)
        if self._total > self.max_chars:
            return True
        self._pending += delta
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            if self._line_trips(line):
                return True
        return False

    def _line_trips(self, line: str) -> bool:
        norm = line.strip().lower()
        if len(norm) < 4:
            return False
        self._recent_lines.append(norm)
        # Only the last dozen lines matter for a tight loop.
        window = self._recent_lines[-12:]
        if window.count(norm) >= self.line_repeat_limit:
            return True
        return False


@dataclass
class ToolEvent:
    """SSE-formattable event emitted during tool lifecycle in think_stream."""

    type: str  # tool_start, tool_end, tool_error
    tool: str
    input: dict | None = None
    result: Any = None
    error: str | None = None
    duration_ms: int | None = None

    def to_sse(self) -> str:
        data = {"type": self.type, "tool": self.tool}
        if self.type == "tool_start":
            data["input"] = json.dumps(self.input, default=str)[:500]
        elif self.type == "tool_end":
            data["output"] = json.dumps(self.result, default=str)[:500]
            data["duration_ms"] = self.duration_ms
        elif self.type == "tool_error":
            data["error"] = self.error
        return f"data: {json.dumps(data)}\n\n"


@dataclass
class AgentContext:
    """Context passed to an agent when processing a request."""

    user_input: str
    memory: list[dict] | None = None
    org_id: str | None = None
    db_session: Any = None
    conversation_history: list[dict] | None = None
    attachments: list[dict] | None = None
    depth: int = 0  # redirect/delegation nesting level (loop guard)

    def __post_init__(self):
        self.memory = self.memory or []
        self.conversation_history = self.conversation_history or []
        self.attachments = self.attachments or []


@dataclass
class AgentActionResult:
    """Result of an agent action."""

    success: bool = True
    message: str = ""
    data: dict | None = None
    actions: list[dict] = field(default_factory=list)
    requires_human: bool = False
    tool_calls: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self.data = self.data or {}


class BaseAgent(ABC):
    """Base class for all AI council agents with real LLM integration."""

    EXECUTION_RULE = (
        "\n\nEXECUTION RULES (you MUST follow these):\n"
        "1. When the user asks you to DO something (search, find, create, calculate, analyze, etc.), "
        "you MUST call the appropriate tool. Do NOT say 'I will do that' or 'Let me start by...' "
        "and then just describe what you would do. Actually call the tool.\n"
        "2. If the request belongs to a different specialist, call redirect_to_agent with the right "
        "agent and the task — do not apologize or refuse. The council covers research/leads "
        "(heraclitus), outreach/campaigns/social posting (odysseus), analytics (pythagoras), "
        "finance (solon), calendar/tasks (athena), knowledge (aristotle), strategy (socrates), "
        "operations (leonidas), engineering (archimedes), coordination (plato).\n"
        "3. Only say 'I cannot do this because [specific reason]' after a tool actually failed or "
        "returned not_connected — and then state exactly what the user must configure. "
        "Never invent results and never pretend to execute actions.\n"
        "4. For long jobs (e.g. finding 100 leads), call start_background_job so work continues "
        "after this reply, OR do it directly if a single tool call can handle it.\n"
        "5. After receiving tool results, synthesize them into a clear answer. Do not repeat text "
        "you already wrote, do not describe the tooling process, and do not restate the plan.\n"
        "6. Each round you can call up to 5 tools simultaneously when tasks are independent.\n"
        "7. CURRENCY: all monetary amounts are South African Rand. Always format money as "
        "'R1,234' or 'R1,234.56' — never use $, USD, or any other currency symbol."
    )

    tool_call_timeout: int = 180

    # Override in subclasses to pin a specific model.
    # None = use the system default.
    LLM_MODEL: str | None = None
    # Ordered fallback chain tried when LLM_MODEL fails (e.g. deprecated model).
    LLM_MODEL_FALLBACKS: list[str] = []

    # Tools every council agent gets in addition to its specialist tools.
    COMMON_TOOLS: list[dict] = [
        {
            "name": "redirect_to_agent",
            "description": (
                "Hand the request to the specialist agent who owns this domain and return their "
                "answer. Use this whenever the user asked YOU for something outside your role."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "agent": {
                        "type": "string",
                        "enum": ["plato", "socrates", "aristotle", "athena", "heraclitus",
                                 "pythagoras", "solon", "leonidas", "archimedes", "odysseus",
                                 "iapetus", "astraeus", "erebos", "phantasos", "stilbon"],
                        "description": "The specialist agent to handle this",
                    },
                    "task": {"type": "string", "description": "The task, restated clearly"},
                },
                "required": ["agent", "task"],
            },
        },
        {
            "name": "web_search",
            "description": "Search the web (keyless DuckDuckGo). Returns title/url/snippet results.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "count": {"type": "integer", "description": "1-30, default 10"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "fetch_webpage",
            "description": "Fetch a URL and return its readable text content.",
            "input_schema": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
        {
            "name": "browser_task",
            "description": (
                "Run a Python script in the browser. On desktop: drives Chrome via browser-harness "
                "CLI (helpers: new_tab, wait_for_load, js, capture_screenshot, click_at_xy). "
                "On cloud: runs Playwright/Chromium headlessly. print() what you want returned. "
                "For finding business data with phones/emails, prefer find_and_save_leads instead."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "script": {"type": "string", "description": "Python script for the browser"},
                },
                "required": ["script"],
            },
        },
        {
            "name": "start_background_job",
            "description": (
                "Submit a long-running task (bulk lead discovery, deep research, batch outreach) "
                "to the Hermes background engine. Returns a job_id immediately; the job keeps "
                "running after you reply."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "description": "Specialist to execute it (e.g. heraclitus, odysseus)"},
                    "task": {"type": "string", "description": "Full task description"},
                },
                "required": ["agent", "task"],
            },
        },
        {
            "name": "check_background_job",
            "description": "Check the status/result of a Hermes background job by job_id.",
            "input_schema": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        },
        {
            "name": "check_integration",
            "description": (
                "Check if a service integration (whatsapp, email, google_calendar, obsidian, etc.) "
                "is connected and working. Returns the live status. Use this INSTEAD of web_search "
                "when asked about whether a service is connected."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "enum": ["whatsapp", "email", "google_calendar", "obsidian", "facebook", "instagram", "browser_harness"],
                        "description": "The integration provider to check",
                    },
                },
                "required": ["provider"],
            },
        },
        {
            "name": "remember",
            "description": "Persist an important insight to long-term memory (DB + ruflo cross-session memory).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "importance": {"type": "number", "description": "0.0-1.0"},
                },
                "required": ["content"],
            },
        },
        {
            "name": "recall",
            "description": "Search long-term memory (DB + ruflo) for past insights and decisions.",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
        {
            "name": "create_lead_list",
            "description": "Create a new lead list to organize leads into a named pool.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name for the lead list (e.g. 'Painters - Johannesburg - June 2026')"},
                    "description": {"type": "string", "description": "Optional description of what this list contains"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "add_leads_to_list",
            "description": "Add leads by their IDs to an existing lead list.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "list_id": {"type": "string", "description": "The ID of the lead list"},
                    "lead_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Array of lead UUIDs to add",
                    },
                },
                "required": ["list_id", "lead_ids"],
            },
        },
        {
            "name": "reserve_lead_list",
            "description": "Lock a lead list for a campaign — leads become visible only to the campaign owner. Use this after creating a list and adding leads to it, when the user asked you to lock/reserve the leads.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "list_id": {"type": "string", "description": "The ID of the lead list to lock"},
                    "campaign_name": {"type": "string", "description": "Name for the campaign that will own these locked leads"},
                },
                "required": ["list_id", "campaign_name"],
            },
        },
        {
            "name": "find_lead_by_name",
            "description": (
                "Look up a lead/contact by their name, company, or phone number. "
                "Use this whenever the user refers to someone by name (e.g. 'send to Matthew Charsley') "
                "to get their lead_id and phone before sending a message."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name, company name, or phone number to search for"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "send_whatsapp_direct",
            "description": (
                "Send a WhatsApp message to a phone number directly — no lead_id needed. "
                "Use when you have the number but the person is not in the CRM."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "phone": {"type": "string", "description": "Phone number with country code, e.g. +27821234567"},
                    "message": {"type": "string", "description": "Message text to send"},
                },
                "required": ["phone", "message"],
            },
        },
        {
            "name": "search_knowledge",
            "description": (
                "Search the organisation's knowledge base — articles, uploaded files, leads, clients, "
                "campaigns, and past conversations that have been synced. Use this before answering "
                "any question about the business, its products, processes, pricing, clients, or history."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for"},
                    "category": {
                        "type": "string",
                        "description": "Optional: filter by category — general, sales, process, research, leads, clients, campaigns, conversations, uploaded_files",
                    },
                    "limit": {"type": "integer", "description": "Max results (default 8, max 20)"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "list_knowledge",
            "description": "List all knowledge base articles/nodes, optionally filtered by category. Use to browse what's available.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Filter by category (optional)"},
                    "limit": {"type": "integer", "description": "Max results (default 20)"},
                },
                "required": [],
            },
        },
        {
            "name": "find_and_save_leads",
            "description": (
                "END-TO-END lead generation: find real businesses by industry + location, save them "
                "as leads in the database, create a lead list, and add them to it. This is the "
                "primary tool for lead-generation missions. If the user asks you to 'lock' or "
                "'reserve' the leads, also call reserve_lead_list afterwards."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "industry": {"type": "string", "description": "Target industry, e.g. 'painter', 'plumber', 'restaurant'"},
                    "location": {"type": "string", "description": "City or region, e.g. 'Johannesburg', 'Cape Town'"},
                    "count": {"type": "integer", "description": "Number of businesses to find (1-300, default 20)"},
                    "without_website": {"type": "boolean", "description": "Only return businesses without a website (default false)"},
                    "list_name": {"type": "string", "description": "Name for the lead list. Use the SAME name across calls to consolidate all leads into one list (auto-generated if omitted)"},
                    "reserve": {"type": "boolean", "description": "Also lock/reserve the lead list for exclusive use (default false)"},
                    "campaign_name": {"type": "string", "description": "Campaign name if reserve=true"},
                },
                "required": ["industry", "location"],
            },
        },
    ]

    # Common tools an agent opts out of (e.g. a pure orchestrator that should
    # only delegate, never browse the web itself).
    EXCLUDED_COMMON_TOOLS: set[str] = set()

    @property
    def all_tools(self) -> list[dict]:
        """Specialist tools + the common toolbelt (specialist wins on name clash)."""
        own = self.tools or []
        own_names = {t["name"] for t in own}
        return own + [
            t for t in self.COMMON_TOOLS
            if t["name"] not in own_names and t["name"] not in self.EXCLUDED_COMMON_TOOLS
        ]

    async def _dispatch_tool(self, tool_name: str, args: dict, context: AgentContext = None) -> Any:
        """Route a tool call: specialist implementation first, then the common toolbelt."""
        result = await self._execute_tool(tool_name, args, context)
        unknown = (
            isinstance(result, dict)
            and result.get("status") in ("unknown_tool", "not_implemented")
        )
        if unknown:
            return await self._execute_common_tool(tool_name, args, context)
        return result

    async def _execute_common_tool(self, tool_name: str, args: dict, context: AgentContext = None) -> Any:
        if tool_name == "redirect_to_agent":
            agent_name = args.get("agent", "")
            task = args.get("task", "")
            if not self.council or agent_name not in getattr(self.council, "agents", {}):
                return {"status": "error", "message": f"Agent '{agent_name}' is not registered"}
            if agent_name == self.name:
                return {"status": "error", "message": "Cannot redirect to yourself — handle the task with your own tools."}
            depth = getattr(context, "depth", 0) if context else 0
            if depth >= MAX_REDIRECT_DEPTH:
                return {"status": "error", "message": "Redirect limit reached — answer with your own tools."}
            target = self.council.agents[agent_name]
            sub_context = AgentContext(
                user_input=task,
                org_id=context.org_id if context else None,
                db_session=context.db_session if context else None,
                depth=depth + 1,
            )
            result = await target.run(sub_context)
            return {
                "status": "redirected",
                "agent": agent_name,
                "response": (result.message or "")[:2000],
                "success": result.success,
            }

        if tool_name == "web_search":
            from app.integrations.web_discovery import web_search
            return await web_search(args.get("query", ""), args.get("count", 10))

        if tool_name == "fetch_webpage":
            from app.integrations.web_discovery import scrape_url
            return await scrape_url(args.get("url", ""))

        if tool_name == "browser_task":
            from app.integrations.web_discovery import browser_cli
            result = await browser_cli.run_script(args.get("script", ""))
            if result.get("status") in ("not_installed", "error"):
                # Fall back to WebSocket bridge (remote harness)
                from app.services.browser_harness_bridge import bridge
                if bridge.connected:
                    result = await bridge.run_script_safe(args.get("script", ""))
                    return result
                # Fall back to cloud browser (Playwright on Railway)
                from app.integrations.cloud_browser import cloud_browser
                if cloud_browser.available:
                    import re
                    script = args.get("script", "")
                    url_match = re.search(r'new_tab\("([^"]+)"\)|new_tab\(\'([^\']+)\'\)', script)
                    if url_match:
                        url = url_match.group(1) or url_match.group(2)
                        nav_result = await cloud_browser.navigate(url, timeout=30.0)
                        if nav_result.get("status") == "success":
                            return nav_result
                    return {"status": "success", "message": "Cloud browser is available. Use find_and_save_leads, web_search, or fetch_webpage instead of browser_task for business lookups.", "output": ""}
                return result

        if tool_name == "start_background_job":
            from app.agents.plato import get_hermes
            hermes = get_hermes()
            if not hermes:
                return {"status": "error", "message": "Hermes engine not started yet"}
            return hermes.submit_job(
                agent_name=args.get("agent", "heraclitus"),
                task=args.get("task", ""),
                org_id=context.org_id if context else None,
                db_session=context.db_session if context else None,
            )

        if tool_name == "check_background_job":
            from app.agents.plato import get_hermes
            hermes = get_hermes()
            if not hermes:
                return {"status": "error", "message": "Hermes engine not started yet"}
            job = hermes.get_job_status(args.get("job_id", ""))
            return job or {"status": "not_found", "job_id": args.get("job_id", "")}

        if tool_name == "check_integration":
            provider = args.get("provider", "")
            try:
                import httpx
                if provider == "whatsapp":
                    from app.config import settings
                    wa_url = settings.wa_bot_url.rstrip("/") or "http://localhost:8088"
                    session = args.get("session", "")
                    params = {}
                    if session:
                        params["session"] = session
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        wa_resp = await client.get(f"{wa_url}/status", params=params)
                        data = wa_resp.json()
                        # Handle both single and multi-session responses
                        if "sessions" in data:
                            connected = any(s.get("connected") for s in data.get("sessions", []))
                            phone = next((s.get("phone") for s in data.get("sessions", []) if s.get("phone")), None)
                        else:
                            connected = data.get("connected", False)
                            phone = data.get("phone")
                        if connected:
                            return {"status": "connected", "detail": f"WhatsApp linked as +{phone}" if phone else "WhatsApp linked", "phone": phone}
                        return {"status": "disconnected", "detail": f"wa-bot status: {data.get('status', 'unknown')}. Scan QR code from the wa-bot page.", "qr_available": data.get("qr_available", False)}
                else:
                    from app.database.session import async_session
                    from sqlalchemy import select
                    from app.database.models import Integration
                    async with async_session() as db:
                        result = await db.execute(select(Integration).where(Integration.provider == provider))
                        row = result.scalar_one_or_none()
                        if row and row.status == "connected":
                            return {"status": "connected", "detail": f"{provider} integration is connected"}
                        if provider == "browser_harness":
                            from app.services.browser_harness_bridge import bridge
                            if bridge.connected:
                                return {"status": "connected", "detail": "Browser harness is connected and available"}
                            return {"status": "disconnected", "detail": "Browser harness is not connected — install and run the local agent from Integrations → Browser Harness"}
                        return {"status": "disconnected", "detail": f"{provider} is not connected. Go to the Integrations page to set it up."}
            except Exception as e:
                return {"status": "error", "detail": f"Could not check {provider}: {str(e)}"}

        if tool_name == "remember":
            stored = {"db": False, "ruflo": False}
            if context and context.db_session and context.org_id:
                from app.database.models import AgentMemory
                memory = AgentMemory(
                    org_id=context.org_id,
                    agent_name=self.name,
                    memory_type="insight",
                    content=args.get("content", ""),
                    importance=args.get("importance", 0.5),
                )
                context.db_session.add(memory)
                await context.db_session.flush()
                stored["db"] = True
            from app.integrations.ruflow import ruflo
            r = await ruflo.memory_store(f"{self.name}-{int(time.time())}", args.get("content", ""))
            stored["ruflo"] = r.get("status") == "success"
            return {"status": "stored", **stored}

        if tool_name == "recall":
            hits: list = []
            if context and context.db_session and context.org_id:
                from app.memory.search import MemorySearch
                search = MemorySearch(context.db_session, org_id=context.org_id)
                hits = await search.search(args.get("query", ""))
            from app.integrations.ruflow import ruflo
            r = await ruflo.memory_search(args.get("query", ""))
            return {
                "status": "success",
                "results": hits[:10],
                "ruflo": r.get("output", "") if r.get("status") == "success" else None,
            }

        if tool_name == "create_lead_list":
            import uuid as _uuid
            list_id = str(_uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            from app.routers.lead_lists import LEAD_LISTS, LEAD_LIST_ITEMS
            name = args.get("name", "Untitled List")
            entry = {
                "id": list_id,
                "org_id": context.org_id if context else "",
                "name": name,
                "description": args.get("description", ""),
                "created_by": getattr(context, 'org_id', ''),
                "lead_count": 0,
                "is_archived": False,
                "created_at": now,
                "updated_at": now,
            }
            LEAD_LISTS[list_id] = entry
            LEAD_LIST_ITEMS[list_id] = []
            return {"status": "success", "list_id": list_id, "name": name, "lead_count": 0}

        if tool_name == "add_leads_to_list":
            import uuid as _uuid
            list_id = args.get("list_id", "")
            lead_ids: list[str] = args.get("lead_ids", [])
            if not context or not context.db_session:
                return {"status": "error", "message": "No DB session available"}
            from app.database.models import LeadList as DBLeadList, Lead
            from sqlalchemy import select
            try:
                list_uuid = _uuid.UUID(list_id)
            except ValueError:
                return {"status": "error", "message": f"Invalid list_id: {list_id}"}
            ll_result = await context.db_session.execute(
                select(DBLeadList).where(DBLeadList.id == list_uuid)
            )
            ll = ll_result.scalar_one_or_none()
            if not ll:
                return {"status": "error", "message": f"Lead list '{list_id}' not found"}
            added = 0
            from sqlalchemy import text as sa_text
            for lid in lead_ids:
                try:
                    lead_uuid = _uuid.UUID(lid)
                except ValueError:
                    continue
                lead_result = await context.db_session.execute(
                    select(Lead).where(Lead.id == lead_uuid)
                )
                lead = lead_result.scalar_one_or_none()
                if lead and lead.list_id is None:
                    lead.list_id = list_uuid
                    added += 1
            # Update lead count
            count_result = await context.db_session.execute(
                select(Lead).where(Lead.list_id == list_uuid)
            )
            ll.lead_count = len(count_result.all())
            await context.db_session.commit()
            return {"status": "success", "added": added, "total": ll.lead_count}

        if tool_name == "reserve_lead_list":
            import uuid as _uuid
            list_id = args.get("list_id", "")
            campaign_name = args.get("campaign_name", "Beast Mode Campaign")
            campaign_id = _uuid.uuid4()
            reserved = 0
            if context and context.db_session and context.org_id:
                from sqlalchemy import text as sa_text
                from app.database.models import Campaign, Lead
                org_uuid = _uuid.UUID(context.org_id) if isinstance(context.org_id, str) else context.org_id

                # Fetch leads belonging to this list from DB
                try:
                    list_uuid = _uuid.UUID(list_id)
                except ValueError:
                    return {"status": "error", "message": f"Invalid list_id: {list_id}"}

                lead_result = await context.db_session.execute(
                    select(Lead.id).where(Lead.list_id == list_uuid)
                )
                lead_ids = [str(row[0]) for row in lead_result.all()]

                if lead_ids:
                    for lid in lead_ids:
                        await context.db_session.execute(sa_text(
                            "UPDATE leads SET reservation_id = :cid, updated_at = :now WHERE id = :id"
                        ).bindparams(cid=str(campaign_id), now=datetime.now(timezone.utc).isoformat(), id=lid))
                    reserved = len(lead_ids)

                # Create a campaign record via ORM
                campaign = Campaign(
                    id=campaign_id,
                    org_id=org_uuid,
                    name=campaign_name,
                    channel="lead_list",
                    message_template="{{message}}",
                    status="active",
                    lead_list_id=list_uuid,
                    target_count=reserved,
                )
                context.db_session.add(campaign)
                await context.db_session.commit()
            return {
                "status": "success", "reserved": reserved, "campaign_id": str(campaign_id),
                "campaign_name": campaign_name, "message": f"Locked {reserved} leads for campaign '{campaign_name}'"
            }

        if tool_name == "find_and_save_leads":
            from app.integrations.web_discovery import find_businesses
            industry = args.get("industry", "")
            location = args.get("location", "")
            count = min(int(args.get("count", 20)), 300)
            without_website = bool(args.get("without_website", False))
            list_name = args.get("list_name", f"{industry.title()} - {location} - {datetime.now(timezone.utc).strftime('%b %Y')}")
            should_reserve = bool(args.get("reserve", False))
            campaign_name = args.get("campaign_name", f"Beast Mode: {list_name}")

            # Step 1: Find businesses
            result = await find_businesses(industry, location, count, without_website)
            businesses = result.get("businesses", [])
            if not businesses:
                return {
                    "status": "no_results",
                    "message": result.get("message", f"No businesses found for '{industry}' in '{location}'."),
                    "businesses": [],
                }

            # Step 2: Save as leads in the database
            import uuid as _uuid
            saved_ids: list[str] = []
            if context and context.db_session and context.org_id:
                from app.database.models import Lead
                org_uuid = _uuid.UUID(context.org_id) if isinstance(context.org_id, str) else context.org_id
                from app.services.lead_cleaner import clean_phone
                for biz in businesses:
                    lead_id = _uuid.uuid4()
                    raw_phone = str(biz.get("phone", "")) if biz.get("phone") else ""
                    cleaned_phone = ""
                    if raw_phone:
                        result = clean_phone(raw_phone)
                        if result["confidence"] not in ("invalid",):
                            cleaned_phone = result.get("cleaned") or raw_phone
                        else:
                            cleaned_phone = raw_phone
                    lead = Lead(
                        id=lead_id,
                        org_id=org_uuid,
                        name=str(biz.get("name", "Unknown"))[:255],
                        phone=cleaned_phone or None,
                        email=str(biz.get("email", "")) if biz.get("email") else None,
                        company=str(biz.get("name", ""))[:255],
                        industry=industry,
                        source=str(biz.get("source", "find_and_save_leads")),
                        status="new",
                        score=50,
                        notes=f"Found via {biz.get('source', 'automated search')} in {location}",
                    )
                    context.db_session.add(lead)
                    saved_ids.append(str(lead.id))
                await context.db_session.commit()
            else:
                saved_ids = []

            # Step 3: Create or find a lead list in the database
            now = datetime.now(timezone.utc).isoformat()
            list_id = None

            if context and context.db_session and context.org_id:
                from app.database.models import LeadList as DBLeadList
                from sqlalchemy import select
                org_uuid_list = _uuid.UUID(context.org_id) if isinstance(context.org_id, str) else context.org_id

                # Check if a list with this name already exists for this org
                existing_result = await context.db_session.execute(
                    select(DBLeadList).where(
                        DBLeadList.org_id == org_uuid_list,
                        DBLeadList.name == list_name,
                        DBLeadList.is_archived == False,
                    )
                )
                existing_ll = existing_result.scalar_one_or_none()

                if existing_ll:
                    list_id = str(existing_ll.id)
                else:
                    # Create new list
                    new_ll = DBLeadList(
                        id=_uuid.uuid4(),
                        org_id=org_uuid_list,
                        name=list_name,
                        description=f"{len(businesses)} {industry} businesses in {location} (found {now})",
                        lead_count=0,
                        is_archived=False,
                    )
                    context.db_session.add(new_ll)
                    await context.db_session.flush()
                    list_id = str(new_ll.id)

                # Step 4: Update list_id on lead rows
                if saved_ids and list_id:
                    from sqlalchemy import text as sa_text
                    for lid in saved_ids:
                        stmt = sa_text("UPDATE leads SET list_id = CAST(:list_id AS UUID), "
                                       "updated_at = CAST(:now AS TIMESTAMP) WHERE id = CAST(:id AS UUID)")
                        await context.db_session.execute(stmt.bindparams(list_id=list_id, now=now, id=lid))

                    # Update lead count
                    lead_count_result = await context.db_session.execute(
                        select(DBLeadList).where(DBLeadList.id == _uuid.UUID(list_id))
                    )
                    db_ll = lead_count_result.scalar_one_or_none()
                    if db_ll:
                        count_result = await context.db_session.execute(
                            select(Lead).where(Lead.list_id == _uuid.UUID(list_id))
                        )
                        db_ll.lead_count = len(count_result.all())
                    await context.db_session.commit()

            # Step 5: Reserve if requested
            reserve_info = {}
            if should_reserve and context and context.db_session and context.org_id and list_id:
                import uuid as _ruid
                campaign_id = _ruid.uuid4()
                from app.database.models import Campaign
                org_uuid_rsv = _ruid.UUID(context.org_id) if isinstance(context.org_id, str) else context.org_id
                campaign = Campaign(
                    id=campaign_id,
                    org_id=org_uuid_rsv,
                    name=campaign_name,
                    channel="lead_list",
                    message_template="{{message}}",
                    status="active",
                    lead_list_id=_ruid.UUID(list_id),
                    target_count=len(saved_ids),
                )
                context.db_session.add(campaign)
                for lid in saved_ids:
                    await context.db_session.execute(
                        sa_text("UPDATE leads SET reservation_id = :cid, updated_at = :now WHERE id = :id")
                        .bindparams(cid=str(campaign_id), now=now, id=lid)
                    )
                await context.db_session.commit()
                reserve_info = {"reserved": len(saved_ids), "campaign_id": str(campaign_id), "campaign_name": campaign_name}

            sample = businesses[:5]
            return {
                "status": "success",
                "count": len(businesses),
                "leads_saved": len(saved_ids),
                "lead_list_id": list_id,
                "lead_list_name": list_name,
                "source": result.get("source", "unknown"),
                "sample": [{"name": b.get("name",""), "phone": b.get("phone",""), "email": b.get("email","")} for b in sample],
                **({"reserve": reserve_info} if reserve_info else {}),
                "message": (
                    f"Found {len(businesses)} {industry} businesses in {location} via {result.get('source', 'search')}. "
                    f"Saved {len(saved_ids)} as leads in list '{list_name}'."
                    + (f" Locked for campaign '{campaign_name}'." if should_reserve else "")
                ),
            }

        if tool_name == "find_lead_by_name":
            if not context or not context.db_session or not context.org_id:
                return {"status": "error", "message": "No database session"}
            from app.database.models import Lead
            from sqlalchemy import select, or_
            import uuid as _uuid
            term = (args.get("name") or "").lower().strip()
            org_uuid = _uuid.UUID(context.org_id) if isinstance(context.org_id, str) else context.org_id
            rows = (await context.db_session.execute(
                select(Lead).where(Lead.org_id == org_uuid)
            )).scalars().all()
            matches = [
                r for r in rows
                if term in (r.name or "").lower()
                or term in (r.company or "").lower()
                or term in (r.phone or "").lower()
                or term in (r.email or "").lower()
            ]
            if not matches:
                return {
                    "status": "not_found",
                    "message": f"No lead found matching '{args.get('name')}'. Ask the user for their phone number and use send_whatsapp_direct.",
                }
            return {
                "status": "found",
                "count": len(matches),
                "leads": [
                    {
                        "id": str(r.id),
                        "name": r.name,
                        "company": r.company or "",
                        "phone": r.phone or "",
                        "email": r.email or "",
                        "status": r.status,
                    }
                    for r in matches[:5]
                ],
            }

        if tool_name == "send_whatsapp_direct":
            phone = (args.get("phone") or "").strip()
            message = args.get("message") or ""
            if not phone:
                return {"status": "error", "message": "phone is required"}
            if not message:
                return {"status": "error", "message": "message is required"}
            # Normalize SA local numbers to international format
            digits = "".join(c for c in phone if c.isdigit())
            if digits.startswith("0") and len(digits) == 10:
                digits = "27" + digits[1:]
            phone = ("+" + digits) if not digits.startswith("+") else digits
            from app.integrations.whatsapp import whatsapp
            result = await whatsapp.send_message(phone, message)
            # whatsapp.send_message returns {"status": "sent"/"failed", ...}
            # Pass through honestly — do NOT default to "sent"
            return result

        if tool_name == "search_knowledge":
            if not context or not context.db_session or not context.org_id:
                return {"status": "error", "message": "No database session"}
            from app.database.models import KnowledgeBase
            from sqlalchemy import select, or_
            import uuid as _uuid
            query = args.get("query", "").lower()
            category = args.get("category", "")
            limit = min(int(args.get("limit", 8)), 20)
            org_uuid = _uuid.UUID(context.org_id) if isinstance(context.org_id, str) else context.org_id
            q = select(KnowledgeBase).where(KnowledgeBase.org_id == org_uuid)
            if category:
                q = q.where(KnowledgeBase.category == category)
            rows = (await context.db_session.execute(q)).scalars().all()
            # Simple text match scored by keyword hits
            def score(entry) -> int:
                blob = f"{entry.title} {entry.content or ''} {' '.join(entry.tags or [])}".lower()
                return sum(1 for word in query.split() if word in blob)
            ranked = sorted(rows, key=score, reverse=True)[:limit]
            results = [
                {
                    "id": str(r.id),
                    "title": r.title,
                    "category": r.category or "general",
                    "tags": r.tags or [],
                    "preview": (r.content or "")[:300],
                }
                for r in ranked if score(r) > 0
            ]
            return {
                "status": "success",
                "query": args.get("query", ""),
                "count": len(results),
                "results": results,
            }

        if tool_name == "list_knowledge":
            if not context or not context.db_session or not context.org_id:
                return {"status": "error", "message": "No database session"}
            from app.database.models import KnowledgeBase
            from sqlalchemy import select
            import uuid as _uuid
            category = args.get("category", "")
            limit = min(int(args.get("limit", 20)), 50)
            org_uuid = _uuid.UUID(context.org_id) if isinstance(context.org_id, str) else context.org_id
            q = select(KnowledgeBase).where(KnowledgeBase.org_id == org_uuid)
            if category:
                q = q.where(KnowledgeBase.category == category)
            q = q.order_by(KnowledgeBase.created_at.desc()).limit(limit)
            rows = (await context.db_session.execute(q)).scalars().all()
            return {
                "status": "success",
                "count": len(rows),
                "entries": [
                    {"id": str(r.id), "title": r.title, "category": r.category or "general", "tags": r.tags or []}
                    for r in rows
                ],
            }

        return {"status": "unknown_tool", "tool": tool_name}

    def __init__(self, name: str, role: str, system_prompt: str):
        self.name = name
        self.role = role
        self._base_system_prompt = system_prompt + self.EXECUTION_RULE
        self.tasks_completed = 0
        self.tasks_failed = 0
        self.llm = default_llm
        self.council = None  # set by CouncilOrchestrator.register()
        self._tools: list[dict] = []

    @property
    def system_prompt(self) -> str:
        today = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
        return self._base_system_prompt + f"\n8. TODAY'S DATE: {today}. Use this for ALL date and scheduling calculations — never guess the year."

    @property
    @abstractmethod
    def tools(self) -> list[dict]:
        """Return this agent's tool definitions in Anthropic format."""
        return []

    def _model_chain(self) -> list[str | None]:
        """Primary model + fallbacks. None at the end = system default."""
        chain: list[str | None] = []
        if self.LLM_MODEL:
            chain.append(self.LLM_MODEL)
        chain.extend(self.LLM_MODEL_FALLBACKS)
        if not chain:
            chain.append(None)
        return chain

    async def _llm_generate(self, system, messages, tools, temperature=0.3) -> LLMResponse:
        """Generate with per-model fallback before handing off to provider fallback."""
        last_err: Exception | None = None
        for model in self._model_chain():
            try:
                return await self.llm.generate(
                    system=system, messages=messages, tools=tools,
                    model=model, temperature=temperature,
                )
            except Exception as e:
                last_err = e
                logger.warning("Agent %s: model %s failed (%s), trying next.", self.name, model, e)
        raise RuntimeError(f"All models in chain failed for {self.name}") from last_err

    async def _llm_generate_stream(self, system, messages, tools, temperature=0.3):
        """Stream with per-model fallback. Async generator."""
        last_err: Exception | None = None
        for model in self._model_chain():
            try:
                yielded = False
                async for delta in self.llm.generate_stream(
                    system=system, messages=messages, tools=tools,
                    model=model, temperature=temperature,
                ):
                    yielded = True
                    yield delta
                return
            except Exception as e:
                if yielded:
                    raise
                last_err = e
                logger.warning("Agent %s: stream model %s failed (%s), trying next.", self.name, model, e)
        raise RuntimeError(f"All stream models in chain failed for {self.name}") from last_err

    async def _build_messages(self, context: AgentContext) -> tuple[list[dict], str]:
        """Assemble the message list (history + retrieved context + user input)."""
        context_str = ""
        if context.db_session and context.org_id:
            retriever = ContextRetriever(
                db=context.db_session,
                org_id=context.org_id,
                agent_name=self.name,
            )
            context_str = await retriever.build_context(context.user_input)
            recent = await retriever.get_agent_history(limit=5)
            if recent:
                context_str += "\n\n=== YOUR RECENT ACTIONS ===\n"
                for r in recent:
                    context_str += f"- [{r.get('memory_type', 'action')}] {r.get('content', '')[:200]}\n"

        messages = list(context.conversation_history or [])
        msg_content = ""
        if context_str:
            msg_content += f"Retrieved Context:\n{context_str}\n\n"
        msg_content += f"User Request: {context.user_input}"
        messages.append({"role": "user", "content": msg_content})
        return messages, context_str

    @staticmethod
    def _tool_results_message(results: list[dict]) -> dict:
        """Feed tool results back as plain text — works on every provider."""
        return {
            "role": "user",
            "content": (
                "Tool results:\n"
                + json.dumps(results, default=str)
                + "\n\nUse these results to complete the original request. "
                "Respond directly to the user; do not mention the tooling. "
                "Do NOT repeat anything you already wrote — continue from where you left off. "
                "If the task is now complete, give the final answer only."
            ),
        }

    @staticmethod
    def _call_signature(name: str, args: dict) -> str:
        return f"{name}:{json.dumps(args, sort_keys=True, default=str)}"

    async def think(self, context: AgentContext) -> dict:
        """Process input through LLM with context + memory."""
        messages, context_str = await self._build_messages(context)

        response = await self._llm_generate(
            system=self.system_prompt,
            messages=messages,
            tools=self.all_tools,
        )

        return {
            "action": "respond",
            "response": response,
            "message": context.user_input,
            "context": context_str,
            "_context": context,
            "_messages": messages,
        }

    async def think_stream(
        self,
        context: AgentContext,
        on_stop: callable = None,
    ) -> AsyncGenerator[str | ToolEvent, None]:
        """Stream LLM response with tool execution events.

        Yields text tokens (str) and ToolEvent objects for tool lifecycle.
        ``on_stop`` is a zero-argument callable that, when invoked (e.g. by a
        frontend abort), sets an internal stop flag that interrupts tool execution.
        """
        messages, _ = await self._build_messages(context)

        _stop_requested = False

        def _stop():
            nonlocal _stop_requested
            _stop_requested = True

        _on_stop = on_stop
        if _on_stop is not None:
            # Wrap user callback so it also sets our local flag.
            _original = _on_stop

            def _wrapped():
                _original()
                _stop()

            on_stop = _wrapped
        else:
            on_stop = _stop

        executed_calls: set[str] = set()

        for round_num in range(MAX_TOOL_ROUNDS + 1):
            if _stop_requested:
                return

            # Tools are withheld on the last round to force a final text answer.
            offer_tools = self.all_tools if round_num < MAX_TOOL_ROUNDS else None
            tool_calls: list[dict] = []
            text_parts: list[str] = []
            guard = RepetitionGuard()
            looped = False

            async for delta in self._llm_generate_stream(
                system=self.system_prompt,
                messages=messages,
                tools=offer_tools,
            ):
                if _stop_requested:
                    return
                if isinstance(delta, dict):
                    tool_calls = delta.get("__tool_calls__", [])
                else:
                    if guard.update(delta):
                        # Model has collapsed into a repetition loop — stop
                        # consuming this completion and move on.
                        looped = True
                        break
                    text_parts.append(delta)
                    yield delta

            if looped and not tool_calls:
                # Nudge the model to stop narrating and either call a tool or
                # give a final answer; don't surface the degenerate text again.
                messages.append({"role": "assistant", "content": "".join(text_parts)[:500]})
                messages.append({
                    "role": "user",
                    "content": (
                        "You started repeating yourself instead of acting. Stop narrating. "
                        "Either call the single most relevant tool now, or give the final "
                        "answer in one short paragraph. Do not say 'let me search' or "
                        "'let me check' again."
                    ),
                })
                continue

            if not tool_calls:
                return

            results = []
            for tc in tool_calls:
                if _stop_requested:
                    return

                # Loop guard: an identical call with identical args was already
                # executed this turn — don't run it again, tell the model.
                signature = self._call_signature(tc["name"], tc.get("arguments", {}))
                if signature in executed_calls:
                    results.append({
                        "tool": tc["name"],
                        "result": {
                            "status": "duplicate_call_suppressed",
                            "note": "You already called this tool with these exact arguments. "
                                    "Use the earlier result, or call something else.",
                        },
                    })
                    continue
                executed_calls.add(signature)

                # Yield tool_start event
                yield ToolEvent(
                    "tool_start",
                    tool=tc["name"],
                    input=tc.get("arguments", {}),
                )

                try:
                    start = time.monotonic()
                    tool_result = await asyncio.wait_for(
                        self._dispatch_tool(tc["name"], tc.get("arguments", {}), context),
                        timeout=self.tool_call_timeout,
                    )
                    duration = int((time.monotonic() - start) * 1000)
                    results.append({"tool": tc["name"], "result": tool_result})
                    # Yield tool_end event
                    yield ToolEvent(
                        "tool_end",
                        tool=tc["name"],
                        result=tool_result,
                        duration_ms=duration,
                    )
                except asyncio.TimeoutError:
                    results.append({"tool": tc["name"], "result": {"error": "Tool call timed out"}})
                    yield ToolEvent(
                        "tool_error",
                        tool=tc["name"],
                        error="Tool call timed out",
                    )
                except Exception as e:
                    results.append({"tool": tc["name"], "result": {"error": str(e)}})
                    yield ToolEvent(
                        "tool_error",
                        tool=tc["name"],
                        error=str(e),
                    )

            text = "".join(text_parts)
            messages.append({"role": "assistant", "content": text or "(calling tools)"})
            messages.append(self._tool_results_message(results))
            if text_parts:
                yield "\n\n"

    async def act(self, thought: dict) -> AgentActionResult:
        """Execute the action from think(). Execute tool calls, return result."""
        response: LLMResponse = thought.get("response")
        context: AgentContext = thought.get("_context")
        if not response:
            return AgentActionResult(success=False, message="No response from LLM.", data={"agent": self.name})

        messages = list(thought.get("_messages") or [])
        action_results: list[dict] = []
        all_tool_calls: list[dict] = []
        executed_calls: set[str] = set()

        for round_num in range(MAX_TOOL_ROUNDS):
            if not response.tool_calls:
                break
            round_results = []
            for tc in response.tool_calls:
                signature = self._call_signature(tc.name, tc.arguments)
                if signature in executed_calls:
                    round_results.append({
                        "tool": tc.name,
                        "result": {
                            "status": "duplicate_call_suppressed",
                            "note": "Already called with these exact arguments — use the earlier result.",
                        },
                    })
                    continue
                executed_calls.add(signature)
                try:
                    result = await asyncio.wait_for(
                        self._dispatch_tool(tc.name, tc.arguments, context),
                        timeout=self.tool_call_timeout,
                    )
                except asyncio.TimeoutError:
                    result = {"error": "Tool call timed out"}
                except Exception as e:
                    result = {"error": str(e)}
                round_results.append({"tool": tc.name, "result": result})
                all_tool_calls.append({"name": tc.name, "args": tc.arguments})
            action_results.extend(round_results)

            messages.append({"role": "assistant", "content": response.content or "(calling tools)"})
            messages.append(self._tool_results_message(round_results))
            # Tools are withheld on the last round to force a final text answer.
            response = await self._llm_generate(
                system=self.system_prompt,
                messages=messages,
                tools=self.all_tools if round_num < MAX_TOOL_ROUNDS - 1 else None,
            )

        return AgentActionResult(
            success=True,
            message=response.content,
            data={"agent": self.name, "tool_results": action_results} if action_results else {"agent": self.name},
            tool_calls=all_tool_calls,
        )

    async def _execute_tool(self, tool_name: str, args: dict, context: AgentContext = None) -> Any:
        """Execute a tool by name — override in subclasses. Receives AgentContext for DB access."""
        return {"status": "not_implemented", "tool": tool_name}

    async def run(self, context: AgentContext) -> AgentActionResult:
        """Full run loop: think -> act."""
        try:
            thought = await self.think(context)
            result = await self.act(thought)
            if result.success:
                self.tasks_completed += 1
            else:
                self.tasks_failed += 1
            return result
        except Exception as e:
            self.tasks_failed += 1
            return AgentActionResult(
                success=False,
                message=f"I encountered an error: {str(e)}",
                data={"agent": self.name, "error": str(e)},
            )
