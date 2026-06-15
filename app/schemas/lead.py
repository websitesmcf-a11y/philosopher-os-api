from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class LeadCreate(BaseModel):
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    industry: Optional[str] = None
    source: Optional[str] = None
    notes: Optional[str] = None
    tags: list[str] = []
    custom_fields: dict = {}


class LeadUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    industry: Optional[str] = None
    status: Optional[str] = None
    score: Optional[int] = None
    tags: Optional[list[str]] = None
    notes: Optional[str] = None
    assigned_to: Optional[str] = None
    custom_fields: Optional[dict] = None


class LeadResponse(BaseModel):
    id: str
    org_id: str
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    industry: Optional[str] = None
    source: Optional[str] = None
    status: str
    score: int
    tags: list[str] = []
    notes: Optional[str] = None
    assigned_to: Optional[str] = None
    first_contacted_at: Optional[datetime] = None
    last_contacted_at: Optional[datetime] = None
    converted_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class LeadListResponse(BaseModel):
    items: list[LeadResponse]
    total: int
    page: int
    page_size: int
