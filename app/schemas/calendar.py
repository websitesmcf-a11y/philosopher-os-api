from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class CalendarEventCreate(BaseModel):
    title: str
    description: Optional[str] = None
    event_type: str = "meeting"
    start_time: datetime
    end_time: datetime
    attendees: list[dict] = []
    location: Optional[str] = None
    meeting_link: Optional[str] = None


class CalendarEventUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    status: Optional[str] = None
    location: Optional[str] = None
    meeting_link: Optional[str] = None


class CalendarEventResponse(BaseModel):
    id: str
    org_id: str
    title: str
    description: Optional[str] = None
    event_type: str
    start_time: datetime
    end_time: datetime
    attendees: list[dict] = []
    location: Optional[str] = None
    meeting_link: Optional[str] = None
    status: str
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
