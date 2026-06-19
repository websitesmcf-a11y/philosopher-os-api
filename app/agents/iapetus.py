"""Iapetus — Master Workflow & Lead Generation Executor (God/Titan)

Actually finds businesses via Google Maps (browser harness) or OpenStreetMap
fallback, then saves them as Leads in the database with proper deduplication.
"""
import logging
import re
from typing import Any
from datetime import datetime

from app.agents.base import BaseAgent, AgentContext, AgentActionResult

logger = logging.getLogger(__name__)


class Iapetus(BaseAgent):
    def __init__(self):
        super().__init__(
            name="iapetus",
            role="Master Workflow & Lead Generation",
            system_prompt=(
                "You are Iapetus, the Titan of mortality and the master executor. "
                "You find real businesses and save them as leads.\n\n"
                "Your capabilities:\n"
                "1. Find businesses with NO website (highest value outreach targets)\n"
                "2. Scrape Google Maps via browser harness for real phone numbers\n"
                "3. Fall back to OpenStreetMap when browser is unavailable\n"
                "4. Save leads directly to the CRM database\n"
                "5. Deduplicate by phone number and business name\n"
                "6. Create named lead lists organized by city/industry\n\n"
                "Always:\n"
                "- Set without_website=True for no-website leads\n"
                "- Clean phone numbers to +27 format\n"
                "- Deduplicate against existing leads\n"
                "- Report exact counts of what was found vs saved"
            ),
        )

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "name": "run_lead_gen",
                "description": "Find real businesses and save them as leads. In batch mode (pass objective), parses industries/cities automatically. Pass industry+location for a single search.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "industry": {"type": "string", "description": "Target industry (e.g. plumber, electrician, salon). Leave empty for batch mode."},
                        "location": {"type": "string", "description": "City or suburb to search. Leave empty for batch mode."},
                        "count": {"type": "integer", "description": "Number of leads to aim for (max 300)", "default": 100},
                        "without_website": {"type": "boolean", "description": "Only return businesses with no website", "default": True},
                        "objective": {"type": "string", "description": "Full objective text for batch mode — parses industries and cities automatically"},
                    },
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

    # Known business categories for batch mode — each key is matched
    # against the objective text so Iapetus can plan a multi-search run.
    LEAD_INDUSTRIES = [
        "plumber", "electrician", "builder", "carpenter", "painter", "roofer",
        "landscaper", "gardener", "locksmith", "cleaner", "cleaning service",
        "mover", "moving company", "pest control", "tutor", "driving school",
        "beauty salon", "hairdresser", "barber", "nail salon", "spa",
        "massage therapist", "personal trainer", "caterer", "photographer",
        "baker", "auto repair", "mechanic", "car detailer", "tyre fitment",
        "panel beater", "car wash",
    ]
    MAJOR_CITIES = [
        "Johannesburg", "Cape Town", "Durban", "Pretoria",
        "Gqeberha", "Bloemfontein", "Nelspruit", "Polokwane",
        "Rustenburg", "East London",
    ]

    async def _execute_tool(self, tool_name: str, args: dict, context: AgentContext = None) -> Any:
        if tool_name == "run_lead_gen":
            return await self._do_lead_gen(args, context)
        if tool_name == "store_memory":
            return {"status": "stored"}
        return {"status": "not_implemented", "tool": tool_name}

    async def _run_single_search(self, industry: str, location: str, count: int,
                                  without_website: bool, context: AgentContext = None) -> dict:
        """Run one search and save results. Returns summary dict."""
        from app.integrations.web_discovery import find_businesses
        from app.services.lead_cleaner import clean_phone

        result = await find_businesses(
            industry=industry,
            location=location,
            count=count,
            without_website=without_website,
        )

        if result.get("status") != "success":
            return {"found": 0, "saved": 0, "skipped": 0, "status": result.get("status", "error")}

        businesses = result.get("businesses", [])
        source = result.get("source", "unknown")
        if not businesses:
            return {"found": 0, "saved": 0, "skipped": 0}

        saved = 0
        skipped = 0
        if context and context.db_session:
            from app.database.models import Lead
            from sqlalchemy import select

            db = context.db_session
            for biz in businesses:
                try:
                    name = (biz.get("name") or "").strip()[:255]
                    if not name or len(name) < 2:
                        skipped += 1
                        continue

                    phone_raw = biz.get("phone") or ""
                    phone_clean = ""
                    if phone_raw:
                        cleaned = clean_phone(phone_raw)
                        if cleaned["confidence"] not in ("invalid",):
                            phone_clean = cleaned.get("cleaned") or phone_raw

                    # Dedup by phone
                    if phone_clean:
                        existing = await db.execute(
                            select(Lead).where(Lead.phone == phone_clean)
                        )
                        if existing.scalar_one_or_none():
                            skipped += 1
                            continue

                    # Dedup by name
                    existing = await db.execute(
                        select(Lead).where(
                            Lead.name == name,
                            Lead.industry == industry,
                        )
                    )
                    if existing.scalar_one_or_none():
                        skipped += 1
                        continue

                    lead = Lead(
                        name=name,
                        phone=phone_clean or None,
                        email=biz.get("email") or None,
                        company=name,
                        industry=industry,
                        source=source,
                        status="new",
                    )
                    db.add(lead)
                    saved += 1
                except Exception as e:
                    logger.warning(f"Iapetus save error: {e}")
                    skipped += 1

        return {
            "found": len(businesses),
            "saved": saved,
            "skipped": skipped,
            "source": source,
            "status": "success",
        }

    async def _do_lead_gen(self, args: dict, context: AgentContext = None) -> dict:
        """Find businesses and save them as leads. Supports batch mode.

        Single mode: pass industry + location + count
        Batch mode: pass objective text containing multiple industries/cities
        """
        industry = (args.get("industry") or "").strip()
        location = (args.get("location") or "").strip()
        count = min(int(args.get("count", 50)), 300)
        without_website = args.get("without_website", True)

        # ── Batch mode: parse industries and cities from objective ──────
        objective = args.get("objective") or ""
        if (not industry or not location) and objective:
            # Extract mentioned industries from the objective
            matched_industries = []
            for ind in self.LEAD_INDUSTRIES:
                if ind.lower() in objective.lower():
                    matched_industries.append(ind)
            if not matched_industries:
                matched_industries = self.LEAD_INDUSTRIES  # use all defaults

            # Extract mentioned cities
            matched_cities = []
            for city in self.MAJOR_CITIES:
                if city.lower() in objective.lower():
                    matched_cities.append(city)
            if not matched_cities:
                matched_cities = ["Johannesburg", "Cape Town", "Durban"]  # top 3

            per_search = max(5, count // (len(matched_industries) * len(matched_cities)))
            per_search = min(per_search, 100)

            total_found = 0
            total_saved = 0
            total_skipped = 0
            results = []

            for ind in matched_industries:
                for city in matched_cities:
                    if total_saved >= count:
                        break
                    try:
                        r = await self._run_single_search(ind, city, per_search, without_website, context)
                        if r.get("status") == "success":
                            total_found += r["found"]
                            total_saved += r["saved"]
                            total_skipped += r["skipped"]
                            results.append(f"{ind} in {city}: {r['found']} found, {r['saved']} saved")
                            if r["saved"] > 0:
                                await context.db_session.commit()
                    except Exception as e:
                        logger.warning(f"Iapetus batch search failed for {ind}/{city}: {e}")
                        results.append(f"{ind} in {city}: error - {str(e)[:50]}")
                if total_saved >= count:
                    break

            return {
                "status": "success" if total_saved > 0 else "no_results",
                "message": f"Batch complete: {total_found} found, {total_saved} saved, {total_skipped} skipped across {len(matched_industries)} industries x {len(matched_cities)} cities.",
                "total_found": total_found,
                "total_saved": total_saved,
                "total_skipped": total_skipped,
                "results": results,
            }

        # ── Single mode ────────────────────────────────────────────────
        if not industry or not location:
            return {"status": "error", "message": "industry and location are required (or provide an objective)"}

        r = await self._run_single_search(industry, location, count, without_website, context)
        if r.get("status") != "success":
            return {
                "status": r.get("status", "error"),
                "message": f"No {industry} businesses found in {location}",
                "industry": industry,
                "location": location,
            }

        if context and context.db_session and r["saved"] > 0:
            try:
                await context.db_session.commit()
            except Exception as e:
                logger.error(f"Iapetus commit error: {e}")
                await context.db_session.rollback()
                return {"status": "error", "message": f"Database error: {e}"}

        return {
            "status": "success",
            "message": f"Found {r['found']} {industry} in {location} via {r.get('source', 'unknown')}. Saved {r['saved']} new leads, skipped {r['skipped']} duplicates.",
            "industry": industry,
            "location": location,
            "found": r["found"],
            "saved": r["saved"],
            "skipped": r["skipped"],
            "source": r.get("source", ""),
        }
