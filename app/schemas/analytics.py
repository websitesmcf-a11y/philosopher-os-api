from pydantic import BaseModel
from typing import Optional, Any
from datetime import datetime


class DashboardMetrics(BaseModel):
    total_leads: int
    new_leads_today: int
    active_campaigns: int
    conversion_rate: float
    total_clients: int
    mrr: float
    revenue_today: float
    tasks_pending: int
    messages_today: int
    agent_actions_today: int


class LeadAnalytics(BaseModel):
    by_status: dict
    by_source: dict
    by_industry: dict
    conversion_funnel: dict
    trend: list[dict]


class CampaignPerformance(BaseModel):
    campaign_id: str
    name: str
    sent: int
    delivered: int
    replies: int
    conversions: int
    rate: float


class TimeSeriesPoint(BaseModel):
    date: str
    value: float
