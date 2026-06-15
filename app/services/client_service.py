import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.database.models import Client
from app.schemas.client import ClientCreate, ClientUpdate


class ClientService:
    def __init__(self, db: AsyncSession, org_id: str = ""):
        self.db = db
        self.org_id = org_id

    async def list_clients(self, page: int = 1, page_size: int = 20, status: str = None):
        query = select(Client)
        if self.org_id:
            query = query.where(Client.org_id == uuid.UUID(self.org_id))
        if status:
            query = query.where(Client.contract_status == status)
        count_q = select(func.count()).select_from(query.subquery())
        total = (await self.db.execute(count_q)).scalar() or 0
        query = query.order_by(Client.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        items = result.scalars().all()
        return {"items": [self._to_response(c) for c in items], "total": total, "page": page, "page_size": page_size}

    async def create_client(self, data: ClientCreate):
        client = Client(
            id=uuid.uuid4(),
            org_id=uuid.UUID(self.org_id) if self.org_id else uuid.uuid4(),
            **data.model_dump(exclude_none=True),
        )
        self.db.add(client)
        await self.db.flush()
        return self._to_response(client)

    async def get_client(self, client_id: str):
        query = select(Client).where(Client.id == client_id)
        if self.org_id:
            query = query.where(Client.org_id == uuid.UUID(self.org_id))
        result = await self.db.execute(query)
        client = result.scalar_one_or_none()
        if not client:
            from app.core.errors import NotFoundError
            raise NotFoundError("Client not found")
        return self._to_response(client)

    async def update_client(self, client_id: str, data: ClientUpdate):
        query = select(Client).where(Client.id == client_id)
        if self.org_id:
            query = query.where(Client.org_id == uuid.UUID(self.org_id))
        result = await self.db.execute(query)
        client = result.scalar_one_or_none()
        if not client:
            from app.core.errors import NotFoundError
            raise NotFoundError("Client not found")
        for key, val in data.model_dump(exclude_none=True).items():
            setattr(client, key, val)
        await self.db.flush()
        return self._to_response(client)

    async def delete_client(self, client_id: str):
        query = select(Client).where(Client.id == client_id)
        if self.org_id:
            query = query.where(Client.org_id == uuid.UUID(self.org_id))
        result = await self.db.execute(query)
        client = result.scalar_one_or_none()
        if not client:
            from app.core.errors import NotFoundError
            raise NotFoundError("Client not found")
        await self.db.delete(client)

    def _to_response(self, client: Client):
        return {
            "id": str(client.id),
            "org_id": str(client.org_id),
            "lead_id": str(client.lead_id) if client.lead_id else None,
            "name": client.name,
            "phone": client.phone,
            "email": client.email,
            "company": client.company,
            "industry": client.industry,
            "contract_status": client.contract_status,
            "mrr": client.mrr or 0.0,
            "lifetime_value": client.lifetime_value or 0.0,
            "created_at": client.created_at,
            "updated_at": client.updated_at,
        }
