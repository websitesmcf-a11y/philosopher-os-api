import uuid
import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from app.database.models import User
from app.schemas.user import UserCreate, UserUpdate

logger = logging.getLogger(__name__)


class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_users(self, page: int = 1, page_size: int = 20, **filters):
        query = select(User)
        if filters.get("search"):
            like = f"%{filters['search']}%"
            query = query.where(
                or_(User.name.ilike(like), User.email.ilike(like))
            )
        if filters.get("role"):
            query = query.where(User.role == filters["role"])

        count_q = select(func.count()).select_from(query.subquery())
        total = (await self.db.execute(count_q)).scalar() or 0

        query = query.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        items = result.scalars().all()

        return {
            "items": [self._to_response(u) for u in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def get_user(self, user_id: str):
        result = await self.db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            from app.core.errors import NotFoundError
            raise NotFoundError("User not found")
        return self._to_response(user)

    async def get_user_by_clerk_id(self, clerk_id: str):
        result = await self.db.execute(select(User).where(User.clerk_id == clerk_id))
        return result.scalar_one_or_none()

    async def upsert_user(self, data: UserCreate):
        existing = await self.get_user_by_clerk_id(data.clerk_id)
        if existing:
            for key, val in data.model_dump(exclude_none=True, exclude={"clerk_id"}).items():
                setattr(existing, key, val)
            await self.db.flush()
            return self._to_response(existing)
        user = User(id=uuid.uuid4(), **data.model_dump(exclude_none=True))
        self.db.add(user)
        await self.db.flush()
        return self._to_response(user)

    async def update_user(self, user_id: str, data: UserUpdate):
        result = await self.db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            from app.core.errors import NotFoundError
            raise NotFoundError("User not found")
        for key, val in data.model_dump(exclude_none=True).items():
            setattr(user, key, val)
        await self.db.flush()
        return self._to_response(user)

    def _to_response(self, user: User):
        return {
            "id": str(user.id),
            "clerk_id": user.clerk_id,
            "email": user.email,
            "name": user.name,
            "avatar_url": user.avatar_url,
            "role": user.role,
            "preferences": user.preferences or {},
            "created_at": user.created_at,
            "updated_at": user.updated_at,
        }
