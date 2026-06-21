"""Base agent interface with real LLM integration."""
import asyncio
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from app.llm.client import llm as default_llm, LLMResponse
from app.memory.retrieval import ContextRetriever

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

    # Override in subclasses to pin a specific model (e.g. "deepseek-v4-pro").
    # None = use the system default (deepseek-chat / V4 Flash).
    LLM_MODEL: str | None = None

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
            from app.routers.lead_lists import LEAD_LISTS, LEAD_LIST_ITEMS
            list_id = args.get("list_id", "")
            lead_ids: list[str] = args.get("lead_ids", [])
            if list_id not in LEAD_LISTS:
                return {"status": "error", "message": f"Lead list '{list_id}' not found"}
            existing = set(LEAD_LIST_ITEMS.get(list_id, []))
            added = 0
            for lid in lead_ids:
                if lid not in existing:
                    existing.add(lid)
                    added += 1
            LEAD_LIST_ITEMS[list_id] = list(existing)
            LEAD_LISTS[list_id]["lead_count"] = len(existing)
            LEAD_LISTS[list_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
            # Also update list_id on the Lead rows
            if context and context.db_session and lead_ids:
                from sqlalchemy import text as sa_text
                for lid in lead_ids:
                    await context.db_session.execute(sa_text(
                        "UPDATE leads SET list_id = :list_id, updated_at = :now WHERE id = :id"
                    ).bindparams(list_id=list_id, now=datetime.now(timezone.utc).isoformat(), id=lid))
                await context.db_session.commit()
            return {"status": "success", "added": added, "total": len(existing)}

        if tool_name == "reserve_lead_list":
            from app.routers.lead_lists import LEAD_LISTS, LEAD_LIST_ITEMS
            list_id = args.get("list_id", "")
            campaign_name = args.get("campaign_name", "Beast Mode Campaign")
            if list_id not in LEAD_LISTS:
                return {"status": "error", "message": f"Lead list '{list_id}' not found"}
            import uuid as _uuid
            campaign_id = _uuid.uuid4()
            lead_ids = LEAD_LIST_ITEMS.get(list_id, [])
            reserved = 0
            if context and context.db_session and context.org_id:
                from sqlalchemy import text as sa_text
                org_uuid = _uuid.UUID(context.org_id) if isinstance(context.org_id, str) else context.org_id
                if lead_ids:
                    for lid in lead_ids:
                        await context.db_session.execute(sa_text(
                            "UPDATE leads SET reservation_id = :cid, updated_at = :now WHERE id = :id"
                        ).bindparams(cid=str(campaign_id), now=datetime.now(timezone.utc).isoformat(), id=lid))
                    reserved = len(lead_ids)
                # Create a campaign record via ORM
                from app.database.models import Campaign
                campaign = Campaign(
                    id=campaign_id,
                    org_id=org_uuid,
                    name=campaign_name,
                    channel="lead_list",
                    message_template="{{message}}",
                    status="active",
                    lead_list_id=_uuid.UUID(list_id) if isinstance(list_id, str) else list_id,
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

            # Step 3: Create a lead list (or append to existing one with same name)
            from app.routers.lead_lists import LEAD_LISTS as _LL, LEAD_LIST_ITEMS as _LLI
            org_id_str = context.org_id if context else ""
            now = datetime.now(timezone.utc).isoformat()

            # Check if a list with this name already exists for this org
            list_id = None
            for existing_id, existing in list(_LL.items()):
                if existing.get("name") == list_name and existing.get("org_id") == org_id_str:
                    list_id = existing_id
                    break

            if list_id:
                # Append to existing list
                existing_items = set(_LLI.get(list_id, []))
                for lid in (saved_ids or []):
                    if lid not in existing_items:
                        existing_items.add(lid)
                _LLI[list_id] = list(existing_items)
                _LL[list_id]["lead_count"] = len(existing_items)
                _LL[list_id]["updated_at"] = now
            else:
                # Create new list
                list_id = str(_uuid.uuid4())
                list_entry = {
                    "id": list_id,
                    "org_id": org_id_str,
                    "name": list_name,
                    "description": f"{len(businesses)} {industry} businesses in {location} (found {now})",
                    "created_by": org_id_str,
                    "lead_count": len(saved_ids or businesses),
                    "is_archived": False,
                    "created_at": now,
                    "updated_at": now,
                }
                _LL[list_id] = list_entry
                _LLI[list_id] = list(saved_ids) if saved_ids else []

            # Step 4: Update list_id on lead rows (cast to UUID to avoid type mismatch)
            if context and context.db_session and saved_ids:
                from sqlalchemy import text as sa_text
                for lid in saved_ids:
                    stmt = sa_text("UPDATE leads SET list_id = CAST(:list_id AS UUID), "
                                   "updated_at = CAST(:now AS TIMESTAMP) WHERE id = CAST(:id AS UUID)")
                    await context.db_session.execute(stmt.bindparams(list_id=list_id, now=now, id=lid))
                try:
                    await context.db_session.commit()
                except Exception:
                    await context.db_session.rollback()

            # Step 5: Reserve if requested
            reserve_info = {}
            if should_reserve and context and context.db_session and context.org_id:
                import uuid as _ruid
                campaign_id = _ruid.uuid4()
                from app.database.models import Campaign
                org_uuid = _ruid.UUID(context.org_id) if isinstance(context.org_id, str) else context.org_id
                campaign = Campaign(
                    id=campaign_id,
                    org_id=org_uuid,
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

        response = await self.llm.generate(
            system=self.system_prompt,
            messages=messages,
            tools=self.all_tools,
            model=self.LLM_MODEL,
            temperature=0.3,
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

            async for delta in self.llm.generate_stream(
                system=self.system_prompt,
                messages=messages,
                tools=offer_tools,
                model=self.LLM_MODEL,
                temperature=0.3,
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
            response = await self.llm.generate(
                system=self.system_prompt,
                messages=messages,
                tools=self.all_tools if round_num < MAX_TOOL_ROUNDS - 1 else None,
                model=self.LLM_MODEL,
                temperature=0.3,
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
