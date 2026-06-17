from fastapi import APIRouter, Depends, Query
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
import logging
from app.database.session import get_db
from app.core.security import get_current_user, get_current_org
from app.schemas.campaign import CampaignCreate, CampaignUpdate, CampaignResponse, CampaignListResponse, CampaignAddLeads
from app.services.campaign_service import CampaignService
from app.services.notification_service import notify_campaign_event

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/", response_model=CampaignListResponse)
async def list_campaigns(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = CampaignService(db, org_id=org_id)
    return await service.list_campaigns(page=page, page_size=page_size, status=status)


@router.post("/", response_model=CampaignResponse, status_code=201)
async def create_campaign(
    data: CampaignCreate,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = CampaignService(db, org_id=org_id)
    return await service.create_campaign(data)


@router.get("/{campaign_id}", response_model=CampaignResponse)
async def get_campaign(
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = CampaignService(db, org_id=org_id)
    return await service.get_campaign(campaign_id)


@router.patch("/{campaign_id}", response_model=CampaignResponse)
async def update_campaign(
    campaign_id: str,
    data: CampaignUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = CampaignService(db, org_id=org_id)
    return await service.update_campaign(campaign_id, data)


@router.post("/{campaign_id}/leads")
async def add_campaign_leads(
    campaign_id: str,
    data: CampaignAddLeads,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = CampaignService(db, org_id=org_id)
    return await service.add_leads(campaign_id, data.lead_ids)


@router.post("/{campaign_id}/launch")
async def launch_campaign(
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = CampaignService(db, org_id=org_id)
    result = await service.launch_campaign(campaign_id)
    # Notify
    try:
        from app.database.models import Campaign
        from sqlalchemy import select
        c = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
        campaign = c.scalar_one_or_none()
        if campaign:
            await notify_campaign_event(db, "launched", campaign.name, campaign.channel, campaign.target_count)
    except Exception as e:
        logger.error(f"Failed to send campaign notification: {e}")
    return result


@router.post("/{campaign_id}/pause")
async def pause_campaign(
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = CampaignService(db, org_id=org_id)
    result = await service.pause_campaign(campaign_id)
    # Notify
    try:
        from app.database.models import Campaign
        from sqlalchemy import select
        c = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
        campaign = c.scalar_one_or_none()
        if campaign:
            await notify_campaign_event(db, "paused", campaign.name, campaign.channel)
    except Exception as e:
        logger.error(f"Failed to send campaign notification: {e}")
    return result


@router.delete("/{campaign_id}")
async def delete_campaign(
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = CampaignService(db, org_id=org_id)
    return await service.delete_campaign(campaign_id)


@router.get("/{campaign_id}/leads")
async def campaign_leads(
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
    status: Optional[str] = Query(None),
):
    """Return all leads in a campaign with their delivery status."""
    service = CampaignService(db, org_id=org_id)
    return await service.get_campaign_leads(campaign_id, status_filter=status)


@router.get("/{campaign_id}/stats")
async def campaign_stats(
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = CampaignService(db, org_id=org_id)
    return await service.get_stats(campaign_id)
