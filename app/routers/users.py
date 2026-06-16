from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.session import get_db
from app.core.security import get_current_user
from app.schemas.user import UserUpdate
from app.services.user_service import UserService

router = APIRouter()


@router.get("")
async def list_users(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    service = UserService(db)
    return await service.list_users()


@router.get("/{user_id}")
async def get_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    service = UserService(db)
    return await service.get_user(user_id)


@router.patch("/{user_id}")
async def update_user(
    user_id: str,
    data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    service = UserService(db)
    return await service.update_user(user_id, data)
