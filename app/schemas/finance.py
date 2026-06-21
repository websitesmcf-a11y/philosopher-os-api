from pydantic import BaseModel
from typing import Optional
from datetime import datetime, date


class InvoiceCreate(BaseModel):
    client_id: Optional[str] = None
    amount: float
    currency: str = "USD"
    due_date: Optional[date] = None
    lines: list[dict] = []


class InvoiceUpdate(BaseModel):
    status: Optional[str] = None
    paid_at: Optional[datetime] = None


class InvoiceResponse(BaseModel):
    id: str
    org_id: str
    client_id: Optional[str] = None
    invoice_number: str
    amount: float
    currency: str
    status: str
    due_date: Optional[date] = None
    paid_at: Optional[datetime] = None
    lines: list[dict] = []
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ExpenseCreate(BaseModel):
    category: str
    amount: float
    currency: str = "USD"
    description: Optional[str] = None
    incurred_at: date


class ExpenseResponse(BaseModel):
    id: str
    category: str
    amount: float
    currency: str
    description: Optional[str] = None
    incurred_at: date
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class MRRResponse(BaseModel):
    total_mrr: float
    new_business: float
    expansion: float
    churn: float
    contraction: float
    net_new: float
    period: str
