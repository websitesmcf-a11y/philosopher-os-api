import asyncio
import uuid
import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete as sa_delete
from app.database.models import CalendarEvent
from app.database.session import async_session
from app.schemas.calendar import CalendarEventCreate, CalendarEventUpdate

logger = logging.getLogger(__name__)


async def _push_to_google(event: CalendarEvent, action: str = "create", token: str | None = None):
    """Best-effort push a local event change to Google Calendar."""
    if not token:
        from app.integrations.google_calendar import get_access_token
        async with async_session() as db:
            token = await get_access_token(db)
    if not token:
        return
    try:
        from app.integrations.google_calendar import (
            create_event as g_create,
            update_event as g_update,
            delete_event as g_delete,
        )
        if action == "create" and not event.external_id:
            created = await g_create(
                token, event.title, event.description,
                event.start_time, event.end_time, event.location,
            )
            event.external_id = created.get("id")
            async with async_session() as db:
                await db.merge(event)
                await db.flush()
        elif action == "update" and event.external_id:
            await g_update(
                token, event.external_id, event.title, event.description,
                event.start_time, event.end_time, event.location,
            )
        elif action == "delete" and event.external_id:
            await g_delete(token, event.external_id)
    except Exception as e:
        logger.warning(f"Google Calendar push ({action}) failed for event {event.id}: {e}")


class CalendarService:
    def __init__(self, db: AsyncSession, org_id: str):
        self.db = db
        self.org_id = org_id

    async def list_events(
        self,
        page: int = 1,
        page_size: int = 50,
        date_from=None,
        date_to=None,
        **filters,
    ):
        query = select(CalendarEvent).where(CalendarEvent.org_id == self.org_id)
        if date_from:
            query = query.where(CalendarEvent.start_time >= date_from)
        if date_to:
            query = query.where(CalendarEvent.end_time <= date_to)
        if filters.get("event_type"):
            query = query.where(CalendarEvent.event_type == filters["event_type"])
        if filters.get("status"):
            query = query.where(CalendarEvent.status == filters["status"])

        count_q = select(func.count()).select_from(query.subquery())
        total = (await self.db.execute(count_q)).scalar() or 0

        query = query.order_by(CalendarEvent.start_time.asc()).offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        items = result.scalars().all()

        return {
            "items": [self._to_response(e) for e in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def create_event(self, data: CalendarEventCreate, created_by: Optional[str] = None):
        event = CalendarEvent(
            id=uuid.uuid4(),
            org_id=uuid.UUID(self.org_id) if isinstance(self.org_id, str) else self.org_id,
            created_by=uuid.UUID(created_by) if created_by else None,
            **data.model_dump(exclude_none=True),
        )
        self.db.add(event)
        await self.db.flush()
        asyncio.ensure_future(_push_to_google(event, "create"))
        return self._to_response(event)

    async def update_event(self, event_id: str, data: CalendarEventUpdate):
        result = await self.db.execute(
            select(CalendarEvent).where(CalendarEvent.id == event_id, CalendarEvent.org_id == self.org_id)
        )
        event = result.scalar_one_or_none()
        if not event:
            from app.core.errors import NotFoundError
            raise NotFoundError("Event not found")
        for key, val in data.model_dump(exclude_none=True).items():
            setattr(event, key, val)
        await self.db.flush()
        asyncio.ensure_future(_push_to_google(event, "update"))
        return self._to_response(event)

    async def delete_event(self, event_id: str):
        result = await self.db.execute(
            select(CalendarEvent).where(CalendarEvent.id == event_id, CalendarEvent.org_id == self.org_id)
        )
        event = result.scalar_one_or_none()
        if not event:
            from app.core.errors import NotFoundError
            raise NotFoundError("Event not found")
        google_id = event.external_id
        await self.db.delete(event)
        if google_id:
            asyncio.ensure_future(_push_to_google(event, "delete"))

    def _to_response(self, event: CalendarEvent):
        return {
            "id": str(event.id),
            "org_id": str(event.org_id),
            "title": event.title,
            "description": event.description,
            "event_type": event.event_type,
            "start_time": event.start_time,
            "end_time": event.end_time,
            "attendees": event.attendees or [],
            "location": event.location,
            "meeting_link": event.meeting_link,
            "status": event.status,
            "created_at": event.created_at,
            "updated_at": event.updated_at,
        }
