from fastapi import APIRouter, Depends, Query
from app.schemas.lead import LeadCreate, LeadUpdate, LeadResponse, LeadListResponse
from app.services.lead_service import LeadService
from app.database.session import get_db
from app.core.security import get_current_user, get_current_org
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

router = APIRouter()


@router.get("/", response_model=LeadListResponse)
async def list_leads(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    source: Optional[str] = None,
    industry: Optional[str] = None,
    search: Optional[str] = None,
    list_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = LeadService(db, org_id=org_id)
    return await service.list_leads(page=page, page_size=page_size, status=status, source=source, industry=industry, search=search, list_id=list_id)


@router.post("/", response_model=LeadResponse, status_code=201)
async def create_lead(
    data: LeadCreate,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = LeadService(db, org_id=org_id)
    return await service.create_lead(data)


@router.get("/{lead_id}", response_model=LeadResponse)
async def get_lead(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = LeadService(db, org_id=org_id)
    return await service.get_lead(lead_id)


@router.patch("/{lead_id}", response_model=LeadResponse)
async def update_lead(
    lead_id: str,
    data: LeadUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = LeadService(db, org_id=org_id)
    return await service.update_lead(lead_id, data)


@router.delete("/{lead_id}")
async def delete_lead(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = LeadService(db, org_id=org_id)
    await service.delete_lead(lead_id)
    return {"deleted": True}
