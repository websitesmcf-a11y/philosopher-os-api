"""Service-layer tests using a real (SQLite) test session."""

import pytest

from app.services.lead_service import LeadService
from app.services.finance_service import FinanceService
from app.services.campaign_service import CampaignService
from app.schemas.lead import LeadCreate, LeadUpdate
from app.schemas.campaign import CampaignCreate

ORG_ID = "00000000-0000-0000-0000-000000000001"


@pytest.mark.asyncio
async def test_lead_service_crud(test_session):
    svc = LeadService(test_session, org_id=ORG_ID)

    created = await svc.create_lead(LeadCreate(name="Test Lead", email="lead@test.dev"))
    lead_id = created["id"]

    fetched = await svc.get_lead(lead_id)
    assert fetched["name"] == "Test Lead"

    updated = await svc.update_lead(lead_id, LeadUpdate(status="contacted"))
    assert updated["status"] == "contacted"

    listing = await svc.list_leads()
    assert listing["total"] >= 1

    await svc.delete_lead(lead_id)  # raises NotFoundError if missing


@pytest.mark.asyncio
async def test_finance_service_mrr(test_session):
    svc = FinanceService(test_session, org_id=ORG_ID)
    result = await svc.calculate_mrr()
    assert result is not None


@pytest.mark.asyncio
async def test_campaign_service_create_and_stats(test_session):
    svc = CampaignService(test_session, org_id=ORG_ID)
    created = await svc.create_campaign(CampaignCreate(
        name="Test Campaign",
        channel="whatsapp",
        message_template="Hello {name}",
    ))
    campaign_id = created["id"] if isinstance(created, dict) else str(created.id)

    fetched = await svc.get_campaign(campaign_id)
    assert fetched is not None

    stats = await svc.get_stats(campaign_id)
    assert stats is not None
