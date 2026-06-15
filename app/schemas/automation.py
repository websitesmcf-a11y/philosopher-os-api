from pydantic import BaseModel
from typing import Optional, Any
from datetime import datetime


class AutomationRuleCreate(BaseModel):
    name: str
    trigger_event: str
    conditions: dict = {}
    actions: dict
    enabled: bool = True


class AutomationRuleUpdate(BaseModel):
    name: Optional[str] = None
    trigger_event: Optional[str] = None
    conditions: Optional[dict] = None
    actions: Optional[dict] = None
    enabled: Optional[bool] = None


class AutomationRuleResponse(BaseModel):
    id: str
    org_id: str
    name: str
    trigger_event: str
    conditions: dict
    actions: dict
    enabled: bool
    last_run_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ScheduledJobResponse(BaseModel):
    id: str
    org_id: str
    job_type: str
    payload: dict
    scheduled_for: datetime
    status: str
    result: Optional[Any] = None
    error: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
