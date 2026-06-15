from fastapi import APIRouter, Depends, Query
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.session import get_db
from app.core.security import get_current_user, get_current_org
from app.schemas.calendar import CalendarEventCreate, CalendarEventUpdate, CalendarEventResponse
from app.services.calendar_service import CalendarService

router = APIRouter()


@router.get("/events")
async def list_events(
    start: Optional[str] = None,
    end: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = CalendarService(db, org_id=org_id)
    return await service.list_events(page=page, page_size=page_size, date_from=start, date_to=end)


@router.post("/events", status_code=201)
async def create_event(
    data: CalendarEventCreate,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = CalendarService(db, org_id=org_id)
    return await service.create_event(data, created_by=user.get("id"))


@router.patch("/events/{event_id}")
async def update_event(
    event_id: str,
    data: CalendarEventUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = CalendarService(db, org_id=org_id)
    return await service.update_event(event_id, data)


@router.delete("/events/{event_id}")
async def delete_event(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = CalendarService(db, org_id=org_id)
    await service.delete_event(event_id)
    return {"deleted": True}


@router.post("/events/book")
async def book_appointment():
    return {"message": "Appointment booking requires Calendly integration"}
