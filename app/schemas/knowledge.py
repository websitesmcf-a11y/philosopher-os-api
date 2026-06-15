from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class KnowledgeBaseCreate(BaseModel):
    title: str
    content: str
    category: Optional[str] = None
    tags: list[str] = []


class KnowledgeBaseUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[list[str]] = None


class KnowledgeBaseResponse(BaseModel):
    id: str
    org_id: str
    title: str
    content: str
    category: Optional[str] = None
    tags: list[str] = []
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class KnowledgeSearchResult(BaseModel):
    id: str
    title: str
    content: str
    category: Optional[str] = None
    tags: list[str] = []
    score: float = 0.0
