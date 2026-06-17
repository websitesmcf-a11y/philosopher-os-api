from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class CampaignCreate(BaseModel):
    name: str
    channel: str
    industry: Optional[str] = None
    message_template: str
    schedule_config: dict = {}
    target_count: Optional[int] = 0
    lead_list_id: Optional[str] = None


class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    message_template: Optional[str] = None
    status: Optional[str] = None
    schedule_config: Optional[dict] = None
    target_count: Optional[int] = None


class CampaignResponse(BaseModel):
    id: str
    org_id: str
    name: str
    channel: str
    industry: Optional[str] = None
    message_template: str
    status: str
    schedule_config: dict = {}
    target_count: int
    sent_count: int
    reply_count: int
    conversion_count: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class CampaignAddLeads(BaseModel):
    lead_ids: list[str]


class CampaignListResponse(BaseModel):
    items: list[CampaignResponse]
    total: int
    page: int
    page_size: int
