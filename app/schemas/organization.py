from pydantic import BaseModel
from typing import Optional, Any
from datetime import datetime


class OrganizationCreate(BaseModel):
    name: str
    slug: str
    settings: dict = {}


class OrganizationUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    settings: Optional[dict] = None


class OrganizationResponse(BaseModel):
    id: str
    name: str
    slug: str
    settings: dict
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class OrgMemberResponse(BaseModel):
    org_id: str
    user_id: str
    role: str
    permissions: list[str] = []
    joined_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
