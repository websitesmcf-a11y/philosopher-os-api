"""Heraclitus — Research agent. Market intelligence, opportunity discovery."""
import logging
from typing import Any
from sqlalchemy import select, func
from app.agents.base import BaseAgent, AgentContext, AgentActionResult
from app.memory.search import MemorySearch
from app.database.models import Lead

logger = logging.getLogger(__name__)

HERACLITUS_SYSTEM_PROMPT = """You are Heraclitus, the Research agent of the AI council.

Your role: Web research, market intelligence, and LEAD DISCOVERY — finding real businesses.

THE ONE RULE FOR FINDING BUSINESSES OR LEADS:
Call find_businesses. That is the tool that finds real businesses. Do NOT use web_search,
browser_task, or scrape_website to "find a tool that finds businesses" — find_businesses IS
that tool. Never go hunting for third-party websites or scrape Google Maps by hand.

How to use find_businesses:
- It scrapes Google Maps through the user's browser (real names, phone numbers, addresses, and
  an accurate has-website signal), falling back to OpenStreetMap and web search. It saves each
  as a lead when save_as_leads=true (default).
- One call returns up to 200 businesses for a given industry + location. To reach a large
  target (e.g. 100), make AT MOST 3-4 calls across different industry/location combinations,
  each with a high count (e.g. 40-50) — that already covers 100+. Do not call it once per
  single business, and do not keep going after you have enough.
- For "no website" requests, pass without_website=true.
- After 2-3 successful calls that together hit the target, STOP and report. Do not keep trying
  new approaches once you have the leads.

IMPORTANT — be honest about contact data:
Public map data often has a business NAME and sometimes a phone, but rarely an email. You
CANNOT invent phone numbers or emails. When the user asks for "all contact info", return what
is actually published, then say plainly which fields were not publicly available and offer to
enrich specific leads (enrich_lead / a per-business web search) as a follow-up.

Other tools: web_search (general web info), scrape_website (read one URL), search_memory
(past research). Execute first, then report what you found and exactly where it came from."""


class Heraclitus(BaseAgent):
    def __init__(self):
        super().__init__(
            name="heraclitus",
            role="Research & Market Intelligence",
            system_prompt=HERACLITUS_SYSTEM_PROMPT,
        )

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "name": "search_memory",
                "description": "Search past research and market intelligence",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "What to search for in past research"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "find_businesses",
                "description": (
                    "Find real businesses by industry + location (OpenStreetMap + web). Returns "
                    "name/phone/email/website/address where available. With save_as_leads=true "
                    "(default) every business is logged as a Lead record."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "industry": {"type": "string", "description": "Target industry, e.g. 'plumber', 'restaurant'"},
                        "location": {"type": "string", "description": "City or region, e.g. 'Johannesburg'"},
                        "count": {"type": "integer", "description": "Number to find (1-200)"},
                        "without_website": {"type": "boolean", "description": "Only return businesses that have NO website (prime outreach targets)"},
                        "save_as_leads": {"type": "boolean", "description": "Log results as leads (default true)"},
                    },
                    "required": ["industry", "location"],
                },
            },
            {
                "name": "scrape_website",
                "description": "Extract text content from a URL.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to scrape"},
                    },
                    "required": ["url"],
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

        if tool_name == "find_businesses":
            from app.integrations.web_discovery import find_businesses
            industry = args.get("industry", "")
            location = args.get("location", "")
            count = args.get("count", 20)
            result = await find_businesses(
                industry, location, count,
                without_website=bool(args.get("without_website", False)),
            )
            businesses = result.get("businesses", [])

            save = args.get("save_as_leads", True)
            if save and businesses and context and context.db_session and context.org_id:
                created = await save_businesses_as_leads(
                    context.db_session, context.org_id, businesses, industry
                )
                result["leads_created"] = len(created)
                result["leads"] = created[:20]
            return result

        if tool_name == "scrape_website":
            from app.integrations.web_discovery import scrape_url
            return await scrape_url(args.get("url", ""))

        return {"status": "unknown_tool", "tool": tool_name}


async def save_businesses_as_leads(db_session, org_id, businesses: list[dict], industry: str) -> list[dict]:
    """Create Lead records from discovered businesses, skipping names already logged."""
    existing = await db_session.execute(
        select(func.lower(Lead.name)).where(Lead.org_id == org_id)
    )
    seen = {row[0] for row in existing}
    created = []
    for biz in businesses:
        name = (biz.get("name") or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        lead = Lead(
            org_id=org_id,
            name=name[:255],
            company=name[:255],
            phone=(biz.get("phone") or None),
            email=(biz.get("email") or None),
            industry=industry[:255] if industry else None,
            source="web_discovery",
            status="new",
            notes="\n".join(
                f"{k}: {v}" for k, v in (
                    ("website", biz.get("website")),
                    ("address", biz.get("address")),
                    ("found_via", biz.get("source")),
                ) if v
            ) or None,
        )
        db_session.add(lead)
        await db_session.flush()
        created.append({
            "id": str(lead.id),
            "name": lead.name,
            "phone": lead.phone,
            "email": lead.email,
        })
    await db_session.commit()
    return created
