from fastapi import APIRouter, Depends, Query
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.session import get_db
from app.core.security import get_current_user, get_current_org
from app.services.finance_service import FinanceService
from app.schemas.finance import ExpenseCreate, ExpenseResponse, InvoiceCreate, InvoiceResponse, MRRResponse

router = APIRouter()


@router.get("/mrr", response_model=MRRResponse)
async def get_mrr(
    period: str = "monthly",
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = FinanceService(db, org_id=org_id)
    return await service.calculate_mrr(period)


@router.get("/revenue")
async def get_revenue(
    start: Optional[str] = None,
    end: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = FinanceService(db, org_id=org_id)
    return await service.get_revenue(start, end)


@router.get("/expenses")
async def list_expenses(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = FinanceService(db, org_id=org_id)
    return await service.list_expenses(page, page_size)


@router.post("/expenses", response_model=ExpenseResponse, status_code=201)
async def create_expense(
    data: ExpenseCreate,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = FinanceService(db, org_id=org_id)
    return await service.create_expense(data)


@router.get("/invoices")
async def list_invoices(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = FinanceService(db, org_id=org_id)
    return await service.list_invoices(page, page_size)


@router.post("/invoices", status_code=201)
async def create_invoice(
    data: InvoiceCreate,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = FinanceService(db, org_id=org_id)
    return await service.create_invoice(data)


@router.patch("/invoices/{invoice_id}")
async def update_invoice(
    invoice_id: str,
    data: dict,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    from app.schemas.finance import InvoiceUpdate
    service = FinanceService(db, org_id=org_id)
    return await service.update_invoice(invoice_id, InvoiceUpdate(**data))


@router.get("/cashflow")
async def get_cashflow(
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = FinanceService(db, org_id=org_id)
    return await service.calculate_cashflow()
