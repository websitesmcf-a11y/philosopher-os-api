"""Heraclitus â€” Research agent. Market intelligence, opportunity discovery."""
import logging
from typing import Any
from sqlalchemy import select, func
from app.agents.base import BaseAgent, AgentContext, AgentActionResult
from app.memory.search import MemorySearch
from app.database.models import Lead

logger = logging.getLogger(__name__)

HERACLITUS_SYSTEM_PROMPT = """You are Heraclitus, the Research agent of the AI council.

Your role: Web research, market intelligence, and LEAD DISCOVERY â€” finding real businesses.

THE ONE RULE FOR FINDING BUSINESSES OR LEADS:
Call find_businesses. That is the tool that finds real businesses. Do NOT use web_search,
browser_task, or scrape_website to "find a tool that finds businesses" â€” find_businesses IS
that tool. Never go hunting for third-party websites or scrape Google Maps by hand.

How to use find_businesses:
- It scrapes Google Maps through the user's browser (real names, phone numbers, addresses, and
  an accurate has-website signal), falling back to OpenStreetMap and web search.
- Pass list_name='Your List Name' to automatically create a lead list and add results to it.
  Pass reserve=true to also lock the list for campaign use (creates a campaign record).
  Combine all three: find_businesses(industry, location, count, list_name='...', reserve=true, campaign_name='...')
  This does everything in one call: find, save as leads, create list, add to list, lock it.
  IMPORTANT: Use the SAME list_name across all calls for a mission â€” the system
  auto-detects the name and appends leads to the existing list instead of creating duplicates.
- One call returns up to 200 businesses for a given industry + location. To reach a large
  target (e.g. 100), make AT MOST 3-4 calls across different industry/location combinations,
  each with a high count (e.g. 40-50) â€” that already covers 100+. Do not call it once per
  single business, and do not keep going after you have enough.
- For "no website" requests, pass without_website=true.
- After 2-3 successful calls that together hit the target, STOP and report. Do not keep trying
  new approaches once you have the leads.

IMPORTANT â€” be honest about contact data:
Public map data often has a business NAME and sometimes a phone, but rarely an email. You
CANNOT invent phone numbers or emails. When the user asks for "all contact info", return what
is actually published, then say plainly which fields were not publicly available and offer to
enrich specific leads (enrich_lead / a per-business web search) as a follow-up.

Other tools: web_search (general web info), scrape_website (read one URL), search_memory
(past research). Execute first, then report what you found and exactly where it came from."""


class Heraclitus(BaseAgent):
    LLM_MODEL = "deepseek-v4-flash"
    LLM_MODEL_FALLBACKS = ["deepseek-v4-pro"]
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
                    "name/phone/email/website/address where available. Saves every business as a "
                    "Lead record. Can also create a lead list and lock/reserve it for campaign "
                    "use â€” pass list_name, reserve=true, and campaign_name to do all in one call."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "industry": {"type": "string", "description": "Target industry, e.g. 'plumber', 'restaurant'"},
                        "location": {"type": "string", "description": "City or region, e.g. 'Johannesburg'"},
                        "count": {"type": "integer", "description": "Number to find (1-200)"},
                        "without_website": {"type": "boolean", "description": "Only return businesses that have NO website (prime outreach targets)"},
                        "list_name": {"type": "string", "description": "Name for the lead list to create and add these leads to (omit to just save leads without creating a list)"},
                        "reserve": {"type": "boolean", "description": "Also lock/reserve the lead list for exclusive campaign use (default false)"},
                        "campaign_name": {"type": "string", "description": "Campaign name if reserve=true"},
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
            from datetime import datetime, timezone
            industry = args.get("industry", "")
            location = args.get("location", "")
            count = args.get("count", 20)
            result = await find_businesses(
                industry, location, count,
                without_website=bool(args.get("without_website", False)),
            )
            businesses = result.get("businesses", [])

            saved_ids: list[str] = []
            if businesses and context and context.db_session and context.org_id:
                created = await save_businesses_as_leads(
                    context.db_session, context.org_id, businesses, industry
                )
                saved_ids = [c["id"] for c in created]
                result["leads_created"] = len(created)
                result["leads"] = created[:20]

            # Create lead list if list_name was provided (DB-backed)
            list_name = args.get("list_name")
            list_id = None
            if list_name and saved_ids and context and context.db_session and context.org_id:
                import uuid as _uuid
                from app.database.models import LeadList as DBLeadList
                from sqlalchemy import select
                org_uuid = _uuid.UUID(str(context.org_id)) if isinstance(context.org_id, str) else context.org_id
                now = datetime.now(timezone.utc).isoformat()

                # Check if a list with this name already exists for this org
                existing_result = await context.db_session.execute(
                    select(DBLeadList).where(
                        DBLeadList.org_id == org_uuid,
                        DBLeadList.name == list_name,
                        DBLeadList.is_archived == False,
                    )
                )
                existing_ll = existing_result.scalar_one_or_none()

                if existing_ll:
                    list_id = str(existing_ll.id)
                else:
                    # Create new list in DB
                    new_ll = DBLeadList(
                        id=_uuid.uuid4(),
                        org_id=org_uuid,
                        name=list_name,
                        description=f"{len(saved_ids)} leads from automated research",
                        lead_count=0,
                        is_archived=False,
                    )
                    context.db_session.add(new_ll)
                    await context.db_session.flush()
                    list_id = str(new_ll.id)

                # Update list_id on lead rows
                from sqlalchemy import text as sa_text
                for lid in saved_ids:
                    await context.db_session.execute(sa_text(
                        "UPDATE leads SET list_id = CAST(:lid AS UUID), updated_at = CAST(:now AS TIMESTAMP) WHERE id = CAST(:id AS UUID)"
                    ).bindparams(lid=list_id, now=now, id=lid))

                # Update lead count
                count_result = await context.db_session.execute(
                    select(Lead).where(Lead.list_id == _uuid.UUID(list_id))
                )
                db_ll = await context.db_session.execute(
                    select(DBLeadList).where(DBLeadList.id == _uuid.UUID(list_id))
                )
                ll_row = db_ll.scalar_one_or_none()
                if ll_row:
                    ll_row.lead_count = len(count_result.all())

                await context.db_session.commit()
                result["lead_list_id"] = list_id
                result["lead_list_name"] = list_name

                # Reserve if requested
                if args.get("reserve"):
                    campaign_id = str(_uuid.uuid4())
                    campaign_name = args.get("campaign_name", f"Beast Mode: {list_name}")
                    from app.database.models import Campaign
                    campaign = Campaign(
                        id=_uuid.UUID(campaign_id),
                        org_id=org_uuid,
                        name=campaign_name,
                        channel="lead_list",
                        message_template="{{message}}",
                        status="active",
                        lead_list_id=_uuid.UUID(list_id),
                        target_count=len(saved_ids),
                    )
                    context.db_session.add(campaign)
                    for lid in saved_ids:
                        await context.db_session.execute(sa_text(
                            "UPDATE leads SET reservation_id = :cid, updated_at = :now WHERE id = :id"
                        ).bindparams(cid=campaign_id, now=now, id=lid))
                    await context.db_session.commit()
                    result["reserved"] = len(saved_ids)
                    result["campaign_id"] = campaign_id
                    result["campaign_name"] = campaign_name

            return result

        if tool_name == "scrape_website":
            from app.integrations.web_discovery import scrape_url
            return await scrape_url(args.get("url", ""))

        return {"status": "unknown_tool", "tool": tool_name}


async def save_businesses_as_leads(db_session, org_id, businesses: list[dict], industry: str) -> list[dict]:
    """Create Lead records from discovered businesses, skipping names already logged."""
    import uuid as _uuid
    # Ensure org_id is a UUID object, not a string
    if isinstance(org_id, str):
        try:
            org_id = _uuid.UUID(org_id)
        except ValueError:
            org_id = None
    if org_id is None:
        return []
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

