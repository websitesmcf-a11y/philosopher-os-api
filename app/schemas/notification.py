from pydantic import BaseModel
from typing import Optional, Any
from datetime import datetime


class NotificationCreate(BaseModel):
    user_id: str
    type: str
    title: str
    body: Optional[str] = None
    data: dict = {}
    org_id: Optional[str] = None


class NotificationResponse(BaseModel):
    id: str
    org_id: Optional[str] = None
    user_id: str
    type: str
    title: str
    body: Optional[str] = None
    data: dict
    read: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class NotificationListResponse(BaseModel):
    items: list[NotificationResponse]
    total: int
    unread_count: int = 0
