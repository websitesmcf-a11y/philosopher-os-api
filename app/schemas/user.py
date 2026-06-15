from pydantic import BaseModel
from typing import Optional, Any
from datetime import datetime


class UserCreate(BaseModel):
    clerk_id: str
    email: str
    name: str
    avatar_url: Optional[str] = None
    role: str = "member"
    preferences: dict = {}


class UserUpdate(BaseModel):
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    role: Optional[str] = None
    preferences: Optional[dict] = None


class UserResponse(BaseModel):
    id: str
    clerk_id: str
    email: str
    name: str
    avatar_url: Optional[str] = None
    role: str
    preferences: dict
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int
    page: int
    page_size: int
