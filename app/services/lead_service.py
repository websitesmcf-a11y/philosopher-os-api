import uuid
import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete as sa_delete
from app.database.models import Lead
from app.schemas.lead import LeadCreate, LeadUpdate

logger = logging.getLogger(__name__)


class LeadService:
    def __init__(self, db: AsyncSession, org_id: str = ""):
        self.db = db
        self.org_id = org_id

    async def list_leads(self, page: int = 1, page_size: int = 20, **filters):
        query = select(Lead)
        if self.org_id:
            query = query.where(Lead.org_id == uuid.UUID(self.org_id))
        if filters.get("status"):
            query = query.where(Lead.status == filters["status"])
        if filters.get("source"):
            query = query.where(Lead.source == filters["source"])
        if filters.get("industry"):
            query = query.where(Lead.industry == filters["industry"])
        if filters.get("list_id") == "null":
            query = query.where(Lead.list_id.is_(None))
        elif filters.get("list_id"):
            query = query.where(Lead.list_id == filters["list_id"])
        if filters.get("search"):
            search = f"%{filters['search']}%"
            query = query.where(
                Lead.name.ilike(search) | Lead.company.ilike(search) | Lead.email.ilike(search)
            )

        count_q = select(func.count()).select_from(query.subquery())
        total = (await self.db.execute(count_q)).scalar() or 0

        query = query.order_by(Lead.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        items = result.scalars().all()

        return {
            "items": [self._to_response(l) for l in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def create_lead(self, data: LeadCreate):
        lead = Lead(
            id=uuid.uuid4(),
            org_id=uuid.UUID(self.org_id) if self.org_id else uuid.uuid4(),
            **data.model_dump(exclude_none=True),
        )
        self.db.add(lead)
        await self.db.flush()
        return self._to_response(lead)

    async def get_lead(self, lead_id: str):
        query = select(Lead).where(Lead.id == lead_id)
        if self.org_id:
            query = query.where(Lead.org_id == uuid.UUID(self.org_id))
        result = await self.db.execute(query)
        lead = result.scalar_one_or_none()
        if not lead:
            from app.core.errors import NotFoundError
            raise NotFoundError("Lead not found")
        return self._to_response(lead)

    async def update_lead(self, lead_id: str, data: LeadUpdate):
        result = await self.db.execute(
            select(Lead).where(Lead.id == lead_id, Lead.org_id == uuid.UUID(self.org_id))
        )
        lead = result.scalar_one_or_none()
        if not lead:
            from app.core.errors import NotFoundError
            raise NotFoundError("Lead not found")
        for key, val in data.model_dump(exclude_none=True).items():
            setattr(lead, key, val)
        await self.db.flush()
        return self._to_response(lead)

    async def delete_lead(self, lead_id: str):
        result = await self.db.execute(
            select(Lead).where(Lead.id == lead_id, Lead.org_id == uuid.UUID(self.org_id))
        )
        lead = result.scalar_one_or_none()
        if not lead:
            from app.core.errors import NotFoundError
            raise NotFoundError("Lead not found")
        await self.db.delete(lead)

    def _to_response(self, lead: Lead):
        return {
            "id": str(lead.id),
            "org_id": str(lead.org_id),
            "name": lead.name,
            "phone": lead.phone,
            "email": lead.email,
            "company": lead.company,
            "industry": lead.industry,
            "source": lead.source,
            "status": lead.status,
            "score": lead.score or 0,
            "tags": lead.tags or [],
            "notes": lead.notes,
            "assigned_to": str(lead.assigned_to) if lead.assigned_to else None,
            "first_contacted_at": lead.first_contacted_at,
            "last_contacted_at": lead.last_contacted_at,
            "converted_at": lead.converted_at,
            "created_at": lead.created_at,
            "updated_at": lead.updated_at,
        }
