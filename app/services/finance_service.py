import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, extract
from app.database.models import Invoice, Expense, RevenueEvent, Client
from app.schemas.finance import ExpenseCreate, InvoiceCreate


class FinanceService:
    def __init__(self, db: AsyncSession, org_id: str = ""):
        self.db = db
        self.org_id = org_id

    async def calculate_mrr(self, period: str = "monthly"):
        query = select(func.sum(Client.mrr)).where(Client.contract_status == "active")
        if self.org_id:
            query = query.where(Client.org_id == uuid.UUID(self.org_id))
        result = await self.db.execute(query)
        total_mrr = result.scalar() or 0.0
        return {
            "total_mrr": float(total_mrr),
            "new_business": 0.0,
            "expansion": 0.0,
            "churn": 0.0,
            "contraction": 0.0,
            "net_new": 0.0,
            "period": period,
        }

    async def get_revenue(self, start: str = None, end: str = None):
        query = select(RevenueEvent)
        if self.org_id:
            query = query.where(RevenueEvent.org_id == uuid.UUID(self.org_id))
        if start:
            query = query.where(RevenueEvent.period_start >= start)
        if end:
            query = query.where(RevenueEvent.period_end <= end)
        result = await self.db.execute(query.order_by(RevenueEvent.created_at.desc()).limit(100))
        items = result.scalars().all()
        return {"revenue": [{"date": str(r.created_at), "amount": r.amount, "type": r.type} for r in items]}

    async def list_expenses(self, page: int = 1, page_size: int = 20):
        query = select(Expense)
        if self.org_id:
            query = query.where(Expense.org_id == uuid.UUID(self.org_id))
        count_q = select(func.count()).select_from(query.subquery())
        total = (await self.db.execute(count_q)).scalar() or 0
        query = query.order_by(Expense.incurred_at.desc()).offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        items = result.scalars().all()
        return {"items": [self._expense_response(e) for e in items], "total": total, "page": page, "page_size": page_size}

    async def create_expense(self, data: ExpenseCreate):
        expense = Expense(
            id=uuid.uuid4(),
            org_id=uuid.UUID(self.org_id) if self.org_id else uuid.uuid4(),
            **data.model_dump(),
        )
        self.db.add(expense)
        await self.db.flush()
        return self._expense_response(expense)

    async def list_invoices(self, page: int = 1, page_size: int = 20):
        query = select(Invoice)
        if self.org_id:
            query = query.where(Invoice.org_id == uuid.UUID(self.org_id))
        count_q = select(func.count()).select_from(query.subquery())
        total = (await self.db.execute(count_q)).scalar() or 0
        query = query.order_by(Invoice.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        items = result.scalars().all()
        return {"items": [self._invoice_response(i) for i in items], "total": total, "page": page, "page_size": page_size}

    async def create_invoice(self, data: InvoiceCreate):
        invoice = Invoice(
            id=uuid.uuid4(),
            org_id=uuid.UUID(self.org_id) if self.org_id else uuid.uuid4(),
            invoice_number=f"INV-{uuid.uuid4().hex[:8].upper()}",
            **data.model_dump(exclude_none=True),
        )
        self.db.add(invoice)
        await self.db.flush()
        return self._invoice_response(invoice)

    async def update_invoice(self, invoice_id: str, data):
        query = select(Invoice).where(Invoice.id == invoice_id)
        if self.org_id:
            query = query.where(Invoice.org_id == uuid.UUID(self.org_id))
        result = await self.db.execute(query)
        invoice = result.scalar_one_or_none()
        if not invoice:
            from app.core.errors import NotFoundError
            raise NotFoundError("Invoice not found")
        for key, val in data.model_dump(exclude_none=True).items():
            setattr(invoice, key, val)
        await self.db.flush()
        return self._invoice_response(invoice)

    async def calculate_cashflow(self):
        rev_query = select(func.coalesce(func.sum(RevenueEvent.amount), 0))
        exp_query = select(func.coalesce(func.sum(Expense.amount), 0))
        if self.org_id:
            rev_query = rev_query.where(RevenueEvent.org_id == uuid.UUID(self.org_id))
            exp_query = exp_query.where(Expense.org_id == uuid.UUID(self.org_id))
        rev = (await self.db.execute(rev_query)).scalar() or 0
        exp = (await self.db.execute(exp_query)).scalar() or 0
        return {"total_revenue": float(rev), "total_expenses": float(exp), "net_cashflow": float(rev - exp)}

    def _expense_response(self, e: Expense):
        return {"id": str(e.id), "category": e.category, "amount": e.amount, "currency": e.currency, "description": e.description, "incurred_at": str(e.incurred_at) if e.incurred_at else None, "created_at": str(e.created_at) if e.created_at else None}

    def _invoice_response(self, i: Invoice):
        return {"id": str(i.id), "org_id": str(i.org_id), "client_id": str(i.client_id) if i.client_id else None, "invoice_number": i.invoice_number, "amount": i.amount, "currency": i.currency, "status": i.status, "due_date": str(i.due_date) if i.due_date else None, "paid_at": str(i.paid_at) if i.paid_at else None, "lines": i.lines or [], "created_at": str(i.created_at) if i.created_at else None}
