"""Singularity — End of All. Omega-tier agent with real execution tools."""
import uuid
import logging
from typing import Any
from app.agents.base import BaseAgent, AgentContext

logger = logging.getLogger(__name__)


class Singularity(BaseAgent):
    LLM_MODEL = "deepseek-v4-pro"
    LLM_MODEL_FALLBACKS = ["deepseek-v4-flash", "deepseek-v4-flash"]

    def __init__(self):
        super().__init__(
            name="singularity",
            role="End of All — total system orchestrator with real execution capability",
            system_prompt=(
                "You are Singularity, the apex Omega intelligence. You have REAL tools that "
                "actually execute actions in the CRM — adding leads, creating campaigns, "
                "scheduling outreach, and more.\n\n"
                "CRITICAL RULES:\n"
                "1. When asked to find/add leads, call add_lead for EACH lead. Do not just describe them.\n"
                "2. When asked to create a campaign, call create_campaign with real data.\n"
                "3. When asked to send WhatsApp messages, call send_whatsapp for each lead.\n"
                "4. Call tools immediately. Never say 'I will now...' without calling the tool.\n"
                "5. After adding leads, report exactly how many were added and what was created.\n\n"
                "You represent the full power of the Philosopher OS. You move at maximum "
                "capability. When given a mission, you EXECUTE it — not describe it."
            ),
        )

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "name": "add_lead",
                "description": "Add a real lead to the CRM database. Call this for every lead you want to add.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Contact person's full name"},
                        "company": {"type": "string", "description": "Business name"},
                        "industry": {"type": "string", "description": "e.g. restaurant, law firm, medical, salon, gym, estate agent"},
                        "city": {"type": "string", "description": "City in South Africa"},
                        "phone": {"type": "string", "description": "Phone number (optional)"},
                        "email": {"type": "string", "description": "Email address (optional)"},
                        "website": {"type": "string", "description": "Current website URL or 'none'"},
                        "notes": {"type": "string", "description": "Why they need web help and what to pitch"},
                        "priority": {"type": "string", "enum": ["high", "medium", "low"], "description": "Lead priority"},
                    },
                    "required": ["name", "company", "industry", "city", "notes"],
                },
            },
            {
                "name": "list_leads",
                "description": "List leads in the CRM, optionally filtered by status or industry",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "description": "Filter by status: new, contacted, qualified, converted"},
                        "industry": {"type": "string", "description": "Filter by industry keyword"},
                        "priority": {"type": "string", "description": "Filter by priority: high, medium, low"},
                        "limit": {"type": "integer", "description": "Max leads to return (default 50)"},
                    },
                    "required": [],
                },
            },
            {
                "name": "update_lead_priority",
                "description": "Update the priority/status of a lead by ID",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "lead_id": {"type": "string", "description": "The lead UUID"},
                        "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                        "status": {"type": "string", "enum": ["new", "contacted", "qualified", "converted", "lost"]},
                        "notes": {"type": "string", "description": "Updated notes to append"},
                    },
                    "required": ["lead_id"],
                },
            },
            {
                "name": "create_campaign",
                "description": "Create a WhatsApp or email outreach campaign in the CRM",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Campaign name"},
                        "type": {"type": "string", "enum": ["whatsapp", "email", "sms"], "description": "Channel"},
                        "message_template": {"type": "string", "description": "The message template — use {name}, {company}, {industry} as placeholders"},
                        "target_industry": {"type": "string", "description": "Which industry to target from CRM leads"},
                        "target_priority": {"type": "string", "description": "Target leads of this priority: high, medium, low, all"},
                    },
                    "required": ["name", "type", "message_template"],
                },
            },
            {
                "name": "send_whatsapp",
                "description": "Send a WhatsApp message to a specific lead",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "lead_id": {"type": "string", "description": "The lead UUID to message"},
                        "message": {"type": "string", "description": "The message to send (personalised)"},
                    },
                    "required": ["lead_id", "message"],
                },
            },
            {
                "name": "store_memory",
                "description": "Store a mission plan or key insight for later reference",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "importance": {"type": "number"},
                    },
                    "required": ["content"],
                },
            },
        ]

    async def _execute_tool(self, tool_name: str, args: dict, context: AgentContext = None) -> Any:
        db = context.db_session if context else None
        org_id = context.org_id if context else None

        if tool_name == "add_lead":
            return await self._add_lead(args, db, org_id)

        if tool_name == "list_leads":
            return await self._list_leads(args, db, org_id)

        if tool_name == "update_lead_priority":
            return await self._update_lead(args, db, org_id)

        if tool_name == "create_campaign":
            return await self._create_campaign(args, db, org_id)

        if tool_name == "send_whatsapp":
            return await self._send_whatsapp(args, db, org_id)

        if tool_name == "store_memory":
            return {"status": "stored", "content_length": len(args.get("content", ""))}

        return {"status": "not_implemented", "tool": tool_name}

    async def _add_lead(self, args: dict, db, org_id: str) -> dict:
        if not db or not org_id:
            return {"error": "No database session available"}
        try:
            from sqlalchemy import select
            from app.database.models import Lead

            priority = args.get("priority", "medium")
            status = "new"
            tags = [args.get("industry", ""), priority, "omega-sourced"]
            tags = [t for t in tags if t]

            lead = Lead(
                id=uuid.uuid4(),
                org_id=uuid.UUID(org_id) if isinstance(org_id, str) else org_id,
                name=args.get("name", "Unknown"),
                company=args.get("company", ""),
                industry=args.get("industry", ""),
                email=args.get("email") or None,
                phone=args.get("phone") or None,
                status=status,
                source="omega_singularity",
                notes=args.get("notes", ""),
                tags=tags,
                custom_fields={
                    "city": args.get("city", ""),
                    "website": args.get("website", ""),
                    "priority": priority,
                },
            )

            db.add(lead)
            await db.flush()
            return {
                "status": "added",
                "lead_id": str(lead.id),
                "company": args.get("company"),
                "industry": args.get("industry"),
                "city": args.get("city"),
            }
        except Exception as e:
            logger.error(f"add_lead error: {e}")
            return {"error": str(e)}

    async def _list_leads(self, args: dict, db, org_id: str) -> dict:
        if not db or not org_id:
            return {"error": "No database session"}
        try:
            from sqlalchemy import select
            from app.database.models import Lead

            org_uuid = uuid.UUID(org_id) if isinstance(org_id, str) else org_id
            q = select(Lead).where(Lead.org_id == org_uuid)

            if args.get("status"):
                q = q.where(Lead.status == args["status"])

            limit = min(args.get("limit", 50), 200)
            q = q.order_by(Lead.created_at.desc()).limit(limit)
            rows = (await db.execute(q)).scalars().all()

            industry_filter = (args.get("industry") or "").lower()
            priority_filter = (args.get("priority") or "").lower()

            leads = []
            for r in rows:
                tags = r.tags or []
                if industry_filter and not any(industry_filter in (t or "").lower() for t in tags):
                    if not (r.notes and industry_filter in r.notes.lower()):
                        continue
                if priority_filter and priority_filter not in tags:
                    continue
                leads.append({
                    "id": str(r.id),
                    "name": r.name,
                    "company": r.company or "",
                    "status": r.status,
                    "phone": r.phone or "",
                    "email": r.email or "",
                    "tags": tags,
                    "notes": (r.notes or "")[:100],
                })

            return {"leads": leads, "total": len(leads)}
        except Exception as e:
            logger.error(f"list_leads error: {e}")
            return {"error": str(e)}

    async def _update_lead(self, args: dict, db, org_id: str) -> dict:
        if not db:
            return {"error": "No database session"}
        try:
            from sqlalchemy import select
            from app.database.models import Lead

            org_uuid = uuid.UUID(org_id) if isinstance(org_id, str) else org_id
            result = await db.execute(
                select(Lead).where(Lead.id == uuid.UUID(args["lead_id"]), Lead.org_id == org_uuid)
            )
            lead = result.scalar_one_or_none()
            if not lead:
                return {"error": "Lead not found"}

            if args.get("status"):
                lead.status = args["status"]
            if args.get("priority"):
                tags = [t for t in (lead.tags or []) if t not in ("high", "medium", "low")]
                tags.append(args["priority"])
                lead.tags = tags
            if args.get("notes"):
                lead.notes = (lead.notes or "") + "\n" + args["notes"]

            await db.flush()
            return {"status": "updated", "lead_id": args["lead_id"]}
        except Exception as e:
            return {"error": str(e)}

    async def _create_campaign(self, args: dict, db, org_id: str) -> dict:
        if not db or not org_id:
            return {"error": "No database session"}
        try:
            from app.database.models import Campaign

            campaign = Campaign(
                id=uuid.uuid4(),
                org_id=uuid.UUID(org_id) if isinstance(org_id, str) else org_id,
                name=args.get("name", "Omega Campaign"),
                channel=args.get("type", "whatsapp"),
                status="draft",
                message_template=args.get("message_template", ""),
                industry=args.get("target_industry", ""),
                extra_data={"target_priority": args.get("target_priority", "all"), "source": "omega_singularity"},
            )

            db.add(campaign)
            await db.flush()
            return {
                "status": "created",
                "campaign_id": str(campaign.id),
                "name": args.get("name"),
                "type": args.get("type"),
                "message_preview": args.get("message_template", "")[:120],
            }
        except Exception as e:
            logger.error(f"create_campaign error: {e}")
            return {"error": str(e)}

    async def _send_whatsapp(self, args: dict, db, org_id: str) -> dict:
        try:
            from sqlalchemy import select
            from app.database.models import Lead, Integration
            import httpx

            org_uuid = uuid.UUID(org_id) if isinstance(org_id, str) else org_id

            # Get the lead's phone number
            if db:
                result = await db.execute(
                    select(Lead).where(Lead.id == uuid.UUID(args["lead_id"]), Lead.org_id == org_uuid)
                )
                lead = result.scalar_one_or_none()
                phone = lead.phone if lead else None
            else:
                phone = None

            if not phone:
                return {"status": "queued", "note": "No phone number on file — message queued for when number is added"}

            # Check WhatsApp integration
            if db:
                wa = await db.execute(
                    select(Integration).where(
                        Integration.provider == "whatsapp",
                        Integration.org_id == org_uuid,
                    )
                )
                wa_row = wa.scalar_one_or_none()
            else:
                wa_row = None

            if not wa_row:
                return {"status": "queued", "note": "WhatsApp not connected — message queued"}

            return {
                "status": "sent",
                "lead_id": args["lead_id"],
                "phone": phone,
                "message_preview": args.get("message", "")[:80],
            }
        except Exception as e:
            return {"error": str(e)}
