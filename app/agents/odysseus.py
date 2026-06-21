"""Odysseus â€” Outreach agent. Multi-channel communication, campaigns, follow-ups."""
import logging
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.agents.base import BaseAgent, AgentContext, AgentActionResult
from app.services.campaign_service import CampaignService
from app.schemas.campaign import CampaignCreate
from app.database.models import Campaign, CampaignLead, Lead, ScheduledJob

logger = logging.getLogger(__name__)

ODYSSEUS_SYSTEM_PROMPT = """You are Odysseus, the Outreach agent of the AI council.

Your role: Multi-channel communication. Campaigns. Follow-ups. Lead nurturing.

Personality: Persuasive, persistent, adaptive. You are the master communicator.

Your tools EXECUTE for real â€” they send WhatsApp messages and emails to leads,
post to the connected Facebook Page and Instagram account, and start drip
campaigns that keep sending personalized messages at randomized intervals after
you reply.

How to handle common requests:
- "Send a drip campaign to leads" â†’ start_drip_campaign (it enrolls leads,
  personalizes each message, and schedules sends at random intervals, default
  40-60 minutes apart). If there are no leads yet, redirect_to_agent heraclitus
  to discover some first.
- "Post on Facebook" â†’ post_to_facebook with the message text.
- "Message lead X" â†’ send_message (uses the lead's phone for WhatsApp or email address).
- "Find new leads" â†’ discover_leads (saves real businesses as leads).

When a send returns not_connected, tell the user exactly which connection to
set up on the Connections page. Report real counts â€” sent, skipped, failed â€”
never round up."""


class Odysseus(BaseAgent):
    LLM_MODEL = "deepseek-v4-flash"
    LLM_MODEL_FALLBACKS = ["deepseek-v4-pro"]
    def __init__(self):
        super().__init__(
            name="odysseus",
            role="Outreach & Communications",
            system_prompt=ODYSSEUS_SYSTEM_PROMPT,
        )

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "name": "list_campaigns",
                "description": "List outreach campaigns (optionally by status: draft/active/paused/completed)",
                "input_schema": {
                    "type": "object",
                    "properties": {"status": {"type": "string"}},
                },
            },
            {
                "name": "create_campaign",
                "description": "Create a new outreach campaign (draft â€” use start_drip_campaign to launch sends)",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "channel": {"type": "string", "enum": ["whatsapp", "email", "facebook", "instagram", "linkedin"]},
                        "message_template": {"type": "string"},
                        "industry": {"type": "string"},
                    },
                    "required": ["name", "channel", "message_template"],
                },
            },
            {
                "name": "start_drip_campaign",
                "description": (
                    "Start a drip campaign that REALLY sends personalized messages to leads, one "
                    "at a time, at randomized intervals (default 40-60 minutes apart). Enrolls "
                    "matching leads, personalizes the template per lead with the LLM, and keeps "
                    "running in the background until every lead is messaged. Use {name}, "
                    "{company}, {industry} placeholders in the template."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Campaign name"},
                        "channel": {"type": "string", "enum": ["whatsapp", "email"], "description": "Delivery channel"},
                        "message_template": {"type": "string", "description": "Base message; personalized per lead"},
                        "lead_count": {"type": "integer", "description": "How many leads to enroll (random pick). Omit for all."},
                        "industry": {"type": "string", "description": "Only enroll leads from this industry"},
                        "interval_min_minutes": {"type": "integer", "description": "Minimum gap between sends (default 40)"},
                        "interval_max_minutes": {"type": "integer", "description": "Maximum gap between sends (default 60)"},
                    },
                    "required": ["name", "channel", "message_template"],
                },
            },
            {
                "name": "send_message",
                "description": "Send a direct message to a lead NOW â€” WhatsApp (uses their phone) or email (uses their address). Really delivers.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "lead_id": {"type": "string"},
                        "channel": {"type": "string", "enum": ["whatsapp", "email"]},
                        "body": {"type": "string"},
                        "subject": {"type": "string", "description": "Email subject (email channel only)"},
                    },
                    "required": ["lead_id", "channel", "body"],
                },
            },
            {
                "name": "batch_outreach",
                "description": "Send a message to multiple leads at once over WhatsApp or email. Really delivers; returns sent/skipped/failed counts.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "lead_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of lead IDs to message",
                        },
                        "channel": {"type": "string", "enum": ["whatsapp", "email"], "description": "Channel to use"},
                        "body": {"type": "string", "description": "Message body to send"},
                    },
                    "required": ["lead_ids", "channel", "body"],
                },
            },
            {
                "name": "add_leads_to_campaign",
                "description": (
                    "Enroll existing leads into an existing campaign and start sending. Select "
                    "leads by industry and/or status (or pass explicit lead_ids). The drip "
                    "scheduler then sends each one a personalized message at the campaign's "
                    "interval. Use when the user says 'put these leads in the campaign'."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "campaign_id": {"type": "string", "description": "Target campaign ID"},
                        "lead_ids": {"type": "array", "items": {"type": "string"}, "description": "Explicit lead IDs (optional)"},
                        "industry": {"type": "string", "description": "Enroll all leads in this industry (optional)"},
                        "status": {"type": "string", "description": "Enroll all leads with this status, e.g. 'new' (optional)"},
                        "limit": {"type": "integer", "description": "Max leads to enroll (optional)"},
                    },
                    "required": ["campaign_id"],
                },
            },
            {
                "name": "post_to_facebook",
                "description": "Publish a post on the connected Facebook Page right now via the Graph API.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": "Post text"},
                        "link": {"type": "string", "description": "Optional link to attach"},
                    },
                    "required": ["message"],
                },
            },
            {
                "name": "post_to_instagram",
                "description": "Publish an image post to the connected Instagram Business account. Instagram requires an image URL â€” text-only posts are impossible on their API.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "image_url": {"type": "string", "description": "Public URL of the image"},
                        "caption": {"type": "string", "description": "Post caption"},
                    },
                    "required": ["image_url"],
                },
            },
            {
                "name": "discover_leads",
                "description": "Discover new leads from web sources (OpenStreetMap + web) by industry and location. Creates Lead records with phone/email where published.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "industry": {"type": "string", "description": "Target industry"},
                        "location": {"type": "string", "description": "Geographic location"},
                        "count": {"type": "integer", "description": "Number of leads to discover (1-200)"},
                        "without_website": {"type": "boolean", "description": "Only businesses with NO website"},
                    },
                    "required": ["industry", "location"],
                },
            },
            {
                "name": "enrich_lead",
                "description": "Look up additional information about an existing lead from web sources.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "lead_id": {"type": "string", "description": "ID of the lead to enrich"},
                    },
                    "required": ["lead_id"],
                },
            },
        ]

    async def _execute_tool(self, tool_name: str, args: dict, context: AgentContext = None):
        handled = {
            "list_campaigns", "create_campaign", "start_drip_campaign", "send_message",
            "batch_outreach", "post_to_facebook", "post_to_instagram", "discover_leads",
            "enrich_lead", "add_leads_to_campaign",
        }
        if tool_name not in handled:
            return {"status": "unknown_tool", "tool": tool_name}
        if not context or not context.db_session or not context.org_id:
            return {"status": "requires_db_session", "tool": tool_name}
        db = context.db_session
        org_id = context.org_id

        if tool_name == "list_campaigns":
            svc = CampaignService(db, org_id)
            result = await svc.list_campaigns(status=args.get("status"))
            return {"status": "success", "campaigns": result.get("items", [])}

        if tool_name == "create_campaign":
            svc = CampaignService(db, org_id)
            campaign = await svc.create_campaign(CampaignCreate(
                name=args.get("name", ""),
                channel=args.get("channel", "whatsapp"),
                message_template=args.get("message_template", ""),
                industry=args.get("industry"),
            ))
            return {"status": "created", "campaign_id": campaign.get("id")}

        if tool_name == "start_drip_campaign":
            return await self._start_drip_campaign(db, org_id, args)

        if tool_name == "add_leads_to_campaign":
            return await self._add_leads_to_campaign(db, org_id, args)

        if tool_name == "send_message":
            lead = await self._get_lead(db, org_id, args.get("lead_id", ""))
            if not lead:
                return {"status": "not_found", "message": f"Lead {args.get('lead_id')} not found"}
            from app.services.delivery import deliver_to_lead
            result = await deliver_to_lead(
                db, org_id, lead, args.get("channel", "whatsapp"),
                args.get("body", ""), subject=args.get("subject"),
            )
            await db.commit()
            return result

        if tool_name == "batch_outreach":
            lead_ids = args.get("lead_ids", [])
            channel = args.get("channel", "whatsapp")
            body = args.get("body", "")
            if not lead_ids:
                return {"status": "error", "message": "No lead_ids provided"}
            from app.services.delivery import deliver_to_lead
            results = []
            for lid in lead_ids:
                lead = await self._get_lead(db, org_id, lid)
                if not lead:
                    results.append({"lead_id": lid, "status": "not_found"})
                    continue
                r = await deliver_to_lead(db, org_id, lead, channel, body)
                results.append({"lead_id": lid, "status": r.get("status"), **({"reason": r["reason"]} if r.get("reason") else {})})
            await db.commit()
            return {
                "status": "complete",
                "sent": sum(1 for r in results if r["status"] == "sent"),
                "skipped": sum(1 for r in results if r["status"] == "skipped"),
                "failed": sum(1 for r in results if r["status"] not in ("sent", "skipped")),
                "results": results,
            }

        if tool_name == "post_to_facebook":
            from app.integrations.facebook import post_to_page
            return await post_to_page(db, args.get("message", ""), link=args.get("link"))

        if tool_name == "post_to_instagram":
            from app.integrations.facebook import post_to_instagram
            return await post_to_instagram(db, args.get("image_url", ""), caption=args.get("caption", ""))

        if tool_name == "discover_leads":
            from app.integrations.web_discovery import find_businesses
            from app.agents.heraclitus import save_businesses_as_leads
            industry = args.get("industry", "")
            result = await find_businesses(
                industry, args.get("location", ""), args.get("count", 20),
                without_website=bool(args.get("without_website", False)),
            )
            businesses = result.get("businesses", [])
            if not businesses:
                return result
            created = await save_businesses_as_leads(db, org_id, businesses, industry)
            return {"status": "success", "discovered": created[:20], "count": len(created)}

        if tool_name == "enrich_lead":
            lead = await self._get_lead(db, org_id, args.get("lead_id", ""))
            if not lead:
                return {"status": "not_found", "message": f"Lead {args.get('lead_id')} not found"}
            from app.integrations.web_discovery import web_search
            search = await web_search(f'"{lead.company or lead.name}" contact phone email', count=5)
            enriched = {
                "id": str(lead.id),
                "name": lead.name,
                "company": lead.company,
                "phone": lead.phone,
                "email": lead.email,
                "industry": lead.industry,
                "status": lead.status,
                "web_findings": search.get("results", []),
            }
            return {"status": "success", "lead": enriched}

        return {"status": "unknown_tool", "tool": tool_name}

    @staticmethod
    async def _get_lead(db, org_id, lead_id: str) -> Lead | None:
        try:
            result = await db.execute(
                select(Lead).where(Lead.id == lead_id, Lead.org_id == org_id)
            )
            return result.scalar_one_or_none()
        except Exception:
            return None

    async def _add_leads_to_campaign(self, db, org_id, args: dict) -> dict:
        """Enroll existing leads into an existing campaign and kick off sending."""
        from datetime import datetime, timedelta, timezone
        campaign_id = args.get("campaign_id", "")
        result = await db.execute(
            select(Campaign).where(Campaign.id == campaign_id, Campaign.org_id == org_id)
        )
        campaign = result.scalar_one_or_none()
        if not campaign:
            return {"status": "not_found", "message": f"Campaign {campaign_id} not found"}

        # Resolve which leads to enroll.
        contact_field = Lead.phone if campaign.channel == "whatsapp" else Lead.email
        query = select(Lead).where(Lead.org_id == org_id, contact_field.isnot(None))
        if args.get("lead_ids"):
            query = select(Lead).where(Lead.id.in_(args["lead_ids"]), Lead.org_id == org_id)
        else:
            if args.get("industry"):
                query = query.where(Lead.industry == args["industry"])
            if args.get("status"):
                query = query.where(Lead.status == args["status"])
        leads = list((await db.execute(query)).scalars().all())
        if args.get("limit"):
            leads = leads[: max(1, int(args["limit"]))]
        if not leads:
            return {"status": "no_leads", "message": "No matching leads with a usable contact for this channel."}

        # Skip leads already enrolled.
        existing = await db.execute(
            select(CampaignLead.lead_id).where(CampaignLead.campaign_id == campaign.id)
        )
        already = {row[0] for row in existing}
        added = 0
        for lead in leads:
            if lead.id in already:
                continue
            db.add(CampaignLead(campaign_id=campaign.id, lead_id=lead.id, status="pending"))
            added += 1
        campaign.target_count = (campaign.target_count or 0) + added
        if campaign.status not in ("active",):
            campaign.status = "active"
        await db.flush()

        # Ensure a drip job is scheduled so sending actually starts.
        pending_job = await db.execute(
            select(ScheduledJob).where(
                ScheduledJob.job_type == "campaign_drip_send",
                ScheduledJob.status == "pending",
            )
        )
        has_job = any((j.payload or {}).get("campaign_id") == str(campaign.id)
                      for j in pending_job.scalars().all())
        if not has_job:
            db.add(ScheduledJob(
                org_id=campaign.org_id,
                job_type="campaign_drip_send",
                payload={"campaign_id": str(campaign.id)},
                scheduled_for=datetime.now(timezone.utc) + timedelta(minutes=1),
                status="pending",
            ))
        await db.commit()
        return {
            "status": "enrolled",
            "campaign_id": str(campaign.id),
            "leads_added": added,
            "channel": campaign.channel,
            "note": "Sending starts within a minute and continues at the campaign interval.",
        }

    async def _start_drip_campaign(self, db, org_id, args: dict) -> dict:
        """Create + activate a drip campaign and schedule its first send."""
        channel = args.get("channel", "whatsapp")
        interval_min = max(1, int(args.get("interval_min_minutes") or 40))
        interval_max = max(interval_min, int(args.get("interval_max_minutes") or 60))

        # Pick leads to enroll â€” random order so outreach doesn't look scripted.
        query = select(Lead).where(Lead.org_id == org_id)
        if args.get("industry"):
            query = query.where(Lead.industry == args["industry"])
        contact_field = Lead.phone if channel == "whatsapp" else Lead.email
        query = query.where(contact_field.isnot(None))
        result = await db.execute(query)
        leads = list(result.scalars().all())
        if not leads:
            return {
                "status": "no_leads",
                "message": f"No leads with a {'phone number' if channel == 'whatsapp' else 'email address'} "
                           f"to enroll. Discover leads first (discover_leads or ask Heraclitus).",
            }
        random.shuffle(leads)
        lead_count = args.get("lead_count")
        if lead_count:
            leads = leads[: max(1, int(lead_count))]

        campaign = Campaign(
            org_id=org_id if not isinstance(org_id, str) else uuid.UUID(org_id),
            name=args.get("name", "Drip campaign"),
            channel=channel,
            message_template=args.get("message_template", ""),
            industry=args.get("industry"),
            status="active",
            target_count=len(leads),
            schedule_config={
                "mode": "drip",
                "interval_min_minutes": interval_min,
                "interval_max_minutes": interval_max,
                "personalize": True,
            },
        )
        db.add(campaign)
        await db.flush()

        for lead in leads:
            db.add(CampaignLead(campaign_id=campaign.id, lead_id=lead.id, status="pending"))
        await db.flush()

        # First send goes out within a minute; the scheduler spaces the rest
        # at random interval_min..interval_max gaps.
        first_at = datetime.now(timezone.utc) + timedelta(minutes=1)
        job = ScheduledJob(
            org_id=campaign.org_id,
            job_type="campaign_drip_send",
            payload={"campaign_id": str(campaign.id)},
            scheduled_for=first_at,
            status="pending",
        )
        db.add(job)
        await db.commit()

        return {
            "status": "started",
            "campaign_id": str(campaign.id),
            "channel": channel,
            "enrolled": len(leads),
            "first_send_at": first_at.isoformat(),
            "interval": f"random {interval_min}-{interval_max} minutes between sends",
        }

