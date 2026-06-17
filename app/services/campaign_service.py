import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.database.models import Campaign, CampaignLead, Lead, Integration, ScheduledJob
from app.schemas.campaign import CampaignCreate, CampaignUpdate


class CampaignService:
    def __init__(self, db: AsyncSession, org_id: str = ""):
        self.db = db
        self.org_id = org_id

    async def list_campaigns(self, page: int = 1, page_size: int = 20, status: str = None):
        query = select(Campaign)
        if self.org_id:
            query = query.where(Campaign.org_id == uuid.UUID(self.org_id))
        if status:
            query = query.where(Campaign.status == status)
        count_q = select(func.count()).select_from(query.subquery())
        total = (await self.db.execute(count_q)).scalar() or 0
        query = query.order_by(Campaign.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        items = result.scalars().all()
        return {"items": [self._to_response(c) for c in items], "total": total, "page": page, "page_size": page_size}

    async def create_campaign(self, data: CampaignCreate):
        campaign = Campaign(
            id=uuid.uuid4(),
            org_id=uuid.UUID(self.org_id) if self.org_id else uuid.uuid4(),
            **data.model_dump(exclude_none=True),
        )
        self.db.add(campaign)
        await self.db.flush()
        return self._to_response(campaign)

    async def get_campaign(self, campaign_id: str):
        query = select(Campaign).where(Campaign.id == campaign_id)
        if self.org_id:
            query = query.where(Campaign.org_id == uuid.UUID(self.org_id))
        result = await self.db.execute(query)
        campaign = result.scalar_one_or_none()
        if not campaign:
            from app.core.errors import NotFoundError
            raise NotFoundError("Campaign not found")
        return self._to_response(campaign)

    async def update_campaign(self, campaign_id: str, data: CampaignUpdate):
        query = select(Campaign).where(Campaign.id == campaign_id)
        if self.org_id:
            query = query.where(Campaign.org_id == uuid.UUID(self.org_id))
        result = await self.db.execute(query)
        campaign = result.scalar_one_or_none()
        if not campaign:
            from app.core.errors import NotFoundError
            raise NotFoundError("Campaign not found")
        for key, val in data.model_dump(exclude_none=True).items():
            setattr(campaign, key, val)
        await self.db.flush()
        return self._to_response(campaign)

    async def add_leads(self, campaign_id: str, lead_ids: list[str]):
        """Enroll existing leads into a campaign as pending CampaignLead rows."""
        query = select(Campaign).where(Campaign.id == campaign_id)
        if self.org_id:
            query = query.where(Campaign.org_id == uuid.UUID(self.org_id))
        result = await self.db.execute(query)
        campaign = result.scalar_one_or_none()
        if not campaign:
            from app.core.errors import NotFoundError
            raise NotFoundError("Campaign not found")

        # Resolve the requested leads (scoped to org) so we don't enroll bogus ids.
        lead_query = select(Lead).where(Lead.id.in_(lead_ids))
        if self.org_id:
            lead_query = lead_query.where(Lead.org_id == uuid.UUID(self.org_id))
        leads = list((await self.db.execute(lead_query)).scalars().all())

        # Skip leads already enrolled in this campaign.
        existing = await self.db.execute(
            select(CampaignLead.lead_id).where(CampaignLead.campaign_id == campaign.id)
        )
        already = {row[0] for row in existing}

        added = 0
        for lead in leads:
            if lead.id in already:
                continue
            self.db.add(CampaignLead(campaign_id=campaign.id, lead_id=lead.id, status="pending"))
            added += 1
        campaign.target_count = (campaign.target_count or 0) + added
        await self.db.flush()
        return {
            "added": added,
            "campaign_id": campaign_id,
            "target_count": campaign.target_count or 0,
        }

    async def launch_campaign(self, campaign_id: str):
        query = select(Campaign).where(Campaign.id == campaign_id)
        if self.org_id:
            query = query.where(Campaign.org_id == uuid.UUID(self.org_id))
        result = await self.db.execute(query)
        campaign = result.scalar_one_or_none()
        if not campaign:
            from app.core.errors import NotFoundError
            raise NotFoundError("Campaign not found")
        await self._require_channel_connected(campaign.channel)
        campaign.status = "active"
        await self.db.flush()

        # Schedule a drip-send job so the in-process scheduler actually starts
        # sending to pending leads (one personalized message, then the next at a
        # human-like gap). Avoid duplicating an already-pending job.
        scheduled = False
        pending = await self.db.execute(
            select(ScheduledJob).where(
                ScheduledJob.job_type == "campaign_drip_send",
                ScheduledJob.status == "pending",
            )
        )
        has_job = any(
            (j.payload or {}).get("campaign_id") == str(campaign.id)
            for j in pending.scalars().all()
        )
        if not has_job:
            self.db.add(ScheduledJob(
                org_id=campaign.org_id,
                job_type="campaign_drip_send",
                payload={"campaign_id": str(campaign.id)},
                scheduled_for=datetime.now(timezone.utc) + timedelta(minutes=1),
                status="pending",
            ))
            await self.db.flush()
            scheduled = True

        # Also dispatch the legacy Celery drip task if a broker is available.
        try:
            from app.workers.outreach import execute_campaign_drip
            execute_campaign_drip.delay(campaign_id)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to dispatch Celery task for campaign {campaign_id}: {e}")
        return {"launched": True, "campaign_id": campaign_id, "drip_scheduled": scheduled}

    async def pause_campaign(self, campaign_id: str):
        query = select(Campaign).where(Campaign.id == campaign_id)
        if self.org_id:
            query = query.where(Campaign.org_id == uuid.UUID(self.org_id))
        result = await self.db.execute(query)
        campaign = result.scalar_one_or_none()
        if not campaign:
            from app.core.errors import NotFoundError
            raise NotFoundError("Campaign not found")
        campaign.status = "paused"
        await self.db.flush()
        return {"paused": True, "campaign_id": campaign_id}

    async def _require_channel_connected(self, channel: str):
        """A campaign may only go active when its channel integration is live."""
        from app.core.errors import ConflictError
        result = await self.db.execute(
            select(Integration).where(Integration.provider == channel)
        )
        integration = result.scalar_one_or_none()
        if not integration or integration.status != "connected":
            current = integration.status if integration else "not configured"
            raise ConflictError(
                f"Cannot launch: the '{channel}' integration is {current}. "
                f"Connect it on the Connections page first."
            )

    async def delete_campaign(self, campaign_id: str):
        query = select(Campaign).where(Campaign.id == campaign_id)
        if self.org_id:
            query = query.where(Campaign.org_id == uuid.UUID(self.org_id))
        result = await self.db.execute(query)
        campaign = result.scalar_one_or_none()
        if not campaign:
            from app.core.errors import NotFoundError
            raise NotFoundError("Campaign not found")
        await self.db.delete(campaign)
        await self.db.flush()
        return {"deleted": True, "campaign_id": campaign_id}

    async def get_stats(self, campaign_id: str):
        query = select(Campaign).where(Campaign.id == campaign_id)
        if self.org_id:
            query = query.where(Campaign.org_id == uuid.UUID(self.org_id))
        result = await self.db.execute(query)
        campaign = result.scalar_one_or_none()
        if not campaign:
            from app.core.errors import NotFoundError
            raise NotFoundError("Campaign not found")
        return {
            "campaign_id": campaign_id,
            "name": campaign.name,
            "target": campaign.target_count,
            "sent": campaign.sent_count,
            "replies": campaign.reply_count,
            "conversions": campaign.conversion_count,
            "rate": (campaign.reply_count / campaign.sent_count * 100) if campaign.sent_count else 0,
        }

    async def get_campaign_leads(self, campaign_id: str, status_filter: str | None = None):
        """Return all CampaignLead entries for a campaign with lead details joined in."""
        query = (
            select(CampaignLead, Lead)
            .join(Lead, Lead.id == CampaignLead.lead_id)
            .where(CampaignLead.campaign_id == campaign_id)
        )
        if self.org_id:
            query = query.where(Lead.org_id == uuid.UUID(self.org_id))
        if status_filter:
            query = query.where(CampaignLead.status == status_filter)
        query = query.order_by(CampaignLead.status, CampaignLead.sent_at.desc().nullslast())        result = await self.db.execute(query)
        rows = result.all()
        return {
            "items": [
                {
                    "id": str(cl.lead_id),
                    "name": lead.name,
                    "phone": lead.phone,
                    "email": lead.email,
                    "company": lead.company,
                    "status": cl.status,
                    "sent_at": cl.sent_at.isoformat() if cl.sent_at else None,
                    "replied_at": cl.replied_at.isoformat() if cl.replied_at else None,
                }
                for cl, lead in rows
            ],
            "total": len(rows),
        }

    def _to_response(self, campaign: Campaign):
        return {
            "id": str(campaign.id),
            "org_id": str(campaign.org_id),
            "name": campaign.name,
            "channel": campaign.channel,
            "industry": campaign.industry,
            "message_template": campaign.message_template,
            "status": campaign.status,
            "schedule_config": campaign.schedule_config or {},
            "target_count": campaign.target_count or 0,
            "sent_count": campaign.sent_count or 0,
            "reply_count": campaign.reply_count or 0,
            "conversion_count": campaign.conversion_count or 0,
            "created_at": campaign.created_at,
            "updated_at": campaign.updated_at,
        }
