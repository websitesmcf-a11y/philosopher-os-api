from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.session import get_db
from app.core.security import get_current_user, get_current_org
from app.schemas.analytics import DashboardMetrics
from app.services.finance_service import FinanceService
from app.database.models import Lead, Client, Campaign, Task, Message, Conversation, RevenueEvent, AgentMemory
from sqlalchemy import select, func
from datetime import datetime, timedelta, timezone

router = APIRouter()


@router.get("/dashboard", response_model=DashboardMetrics)
async def dashboard_metrics(
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # Lead counts
    total_leads = (await db.execute(
        select(func.count(Lead.id)).where(Lead.org_id == org_id)
    )).scalar() or 0
    new_leads_today = (await db.execute(
        select(func.count(Lead.id)).where(Lead.org_id == org_id, Lead.created_at >= today_start)
    )).scalar() or 0
    total_clients = (await db.execute(
        select(func.count(Client.id)).where(Client.org_id == org_id)
    )).scalar() or 0
    active_campaigns = (await db.execute(
        select(func.count(Campaign.id)).where(Campaign.org_id == org_id, Campaign.status == "active")
    )).scalar() or 0
    tasks_pending = (await db.execute(
        select(func.count(Task.id)).where(Task.org_id == org_id, Task.status.in_(["pending", "in_progress"]))
    )).scalar() or 0

    # MRR
    mrr_result = await db.execute(
        select(func.coalesce(func.sum(Client.mrr), 0)).where(Client.org_id == org_id, Client.contract_status == "active")
    )
    mrr = float(mrr_result.scalar() or 0.0)

    # Today's revenue
    revenue_today = (await db.execute(
        select(func.coalesce(func.sum(RevenueEvent.amount), 0.0)).where(
            RevenueEvent.org_id == org_id,
            RevenueEvent.created_at >= today_start,
        )
    )).scalar() or 0.0

    # Messages today — count ONLY outbound messages actually delivered to a
    # recipient through the system (campaigns, tasks, agent outreach, beast
    # mode). These are logged against a Conversation on a real delivery channel
    # (whatsapp/email/sms). The in-app "agent" chat console is NOT outreach and
    # must be excluded, otherwise the dashboard inflates the count with chatbot
    # replies. Message has no org_id, so scope via its Conversation.
    # Real send paths use direction "out" (delivery/message_service); some
    # legacy/agent paths use "outbound" — accept both.
    messages_today = (await db.execute(
        select(func.count(Message.id))
        .select_from(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(
            Conversation.org_id == org_id,
            Conversation.channel != "agent",
            Message.direction.in_(["out", "outbound"]),
            Message.created_at >= today_start,
        )
    )).scalar() or 0

    # Agent actions today — count real agent executions recorded in
    # AgentMemory. If nothing tracked, this is honestly 0 (never a duplicate of
    # the message count).
    agent_actions_today = (await db.execute(
        select(func.count(AgentMemory.id)).where(
            AgentMemory.org_id == org_id,
            AgentMemory.created_at >= today_start,
        )
    )).scalar() or 0

    # Conversion rate
    conversion_rate = 0.0
    if total_leads > 0:
        converted = (await db.execute(
            select(func.count(Lead.id)).where(Lead.org_id == org_id, Lead.status == "won")
        )).scalar() or 0
        conversion_rate = round(converted / total_leads * 100, 1)

    return DashboardMetrics(
        total_leads=total_leads,
        new_leads_today=new_leads_today,
        active_campaigns=active_campaigns,
        conversion_rate=conversion_rate,
        total_clients=total_clients,
        mrr=mrr,
        revenue_today=float(revenue_today),
        tasks_pending=tasks_pending,
        messages_today=messages_today,
        agent_actions_today=agent_actions_today,
    )


@router.get("/leads")
async def lead_analytics(
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    by_status = {}
    for s in ["new", "contacted", "interested", "meeting_booked", "proposal_sent", "qualified", "proposal", "negotiation", "won", "lost", "ghosted", "follow_up_needed"]:
        count = (await db.execute(
            select(func.count(Lead.id)).where(Lead.org_id == org_id, Lead.status == s)
        )).scalar() or 0
        if count:
            by_status[s] = count

    by_source = {}
    result = await db.execute(
        select(Lead.source, func.count(Lead.id))
        .where(Lead.org_id == org_id, Lead.source.isnot(None))
        .group_by(Lead.source)
    )
    for source, count in result:
        by_source[source] = count

    by_industry = {}
    result = await db.execute(
        select(Lead.industry, func.count(Lead.id))
        .where(Lead.org_id == org_id, Lead.industry.isnot(None))
        .group_by(Lead.industry)
    )
    for industry, count in result:
        by_industry[industry] = count

    return {"by_status": by_status, "by_source": by_source, "by_industry": by_industry, "conversion_funnel": {}, "trend": []}


@router.get("/campaigns")
async def campaign_analytics(
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    campaigns = (await db.execute(
        select(Campaign).where(Campaign.org_id == org_id).order_by(Campaign.created_at.desc()).limit(50)
    )).scalars().all()

    return {
        "campaigns": [
            {
                "id": str(c.id), "name": c.name, "channel": c.channel,
                "sent": c.sent_count, "replies": c.reply_count,
                "conversions": c.conversion_count,
                "rate": round(c.reply_count / c.sent_count * 100, 1) if c.sent_count else 0,
            }
            for c in campaigns
        ],
        "summary": {
            "total_sent": sum(c.sent_count for c in campaigns),
            "total_replies": sum(c.reply_count for c in campaigns),
            "total_conversions": sum(c.conversion_count for c in campaigns),
        },
    }


@router.get("/trends")
async def trends(
    months: int = 6,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Monthly revenue and lead-growth series from real rows — never fabricated."""
    months = max(1, min(months, 24))
    now = datetime.now(timezone.utc)
    out = []
    for i in range(months - 1, -1, -1):
        # First day of the month i months ago
        year = now.year
        month = now.month - i
        while month <= 0:
            month += 12
            year -= 1
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        if month == 12:
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(year, month + 1, 1, tzinfo=timezone.utc)

        revenue = (await db.execute(
            select(func.coalesce(func.sum(RevenueEvent.amount), 0.0)).where(
                RevenueEvent.org_id == org_id,
                RevenueEvent.created_at >= start,
                RevenueEvent.created_at < end,
            )
        )).scalar() or 0.0
        leads = (await db.execute(
            select(func.count(Lead.id)).where(
                Lead.org_id == org_id,
                Lead.created_at >= start,
                Lead.created_at < end,
            )
        )).scalar() or 0
        out.append({
            "month": start.strftime("%b"),
            "year": year,
            "revenue": float(revenue),
            "leads": leads,
        })
    return {"items": out}


@router.get("/weekly")
async def weekly(
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Last-7-days daily leads, conversions, and revenue from real rows."""
    now = datetime.now(timezone.utc)
    out = []
    for i in range(6, -1, -1):
        day_start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        leads = (await db.execute(
            select(func.count(Lead.id)).where(
                Lead.org_id == org_id, Lead.created_at >= day_start, Lead.created_at < day_end
            )
        )).scalar() or 0
        conversions = (await db.execute(
            select(func.count(Lead.id)).where(
                Lead.org_id == org_id, Lead.status == "won",
                Lead.updated_at >= day_start, Lead.updated_at < day_end,
            )
        )).scalar() or 0
        revenue = (await db.execute(
            select(func.coalesce(func.sum(RevenueEvent.amount), 0.0)).where(
                RevenueEvent.org_id == org_id,
                RevenueEvent.created_at >= day_start,
                RevenueEvent.created_at < day_end,
            )
        )).scalar() or 0.0
        out.append({
            "day": day_start.strftime("%a"),
            "date": day_start.strftime("%Y-%m-%d"),
            "leads": leads,
            "conversions": conversions,
            "revenue": float(revenue),
        })
    return {"items": out}


@router.get("/activity")
async def recent_activity(
    limit: int = 15,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Recent real events across leads, invoices, campaigns, and tasks."""
    limit = max(1, min(limit, 50))
    events: list[dict] = []

    leads = (await db.execute(
        select(Lead).where(Lead.org_id == org_id).order_by(Lead.created_at.desc()).limit(limit)
    )).scalars().all()
    for l in leads:
        events.append({
            "type": "lead",
            "text": f"New lead: {l.name}" + (f" ({l.company})" if l.company else ""),
            "at": l.created_at.isoformat() if l.created_at else None,
        })

    from app.database.models import Invoice
    invoices = (await db.execute(
        select(Invoice).where(Invoice.org_id == org_id).order_by(Invoice.created_at.desc()).limit(limit)
    )).scalars().all()
    for inv in invoices:
        label = "paid" if inv.status == "paid" else inv.status
        events.append({
            "type": "invoice",
            "text": f"Invoice {inv.invoice_number} {label} — ${inv.amount:,.0f}",
            "at": (inv.paid_at or inv.created_at).isoformat() if (inv.paid_at or inv.created_at) else None,
        })

    campaigns = (await db.execute(
        select(Campaign).where(Campaign.org_id == org_id).order_by(Campaign.updated_at.desc()).limit(limit)
    )).scalars().all()
    for c in campaigns:
        events.append({
            "type": "campaign",
            "text": f'Campaign "{c.name}" {c.status}',
            "at": (c.updated_at or c.created_at).isoformat() if (c.updated_at or c.created_at) else None,
        })

    tasks = (await db.execute(
        select(Task).where(Task.org_id == org_id, Task.status == "completed")
        .order_by(Task.updated_at.desc()).limit(limit)
    )).scalars().all()
    for t in tasks:
        events.append({
            "type": "task",
            "text": f"Task completed: {t.title}",
            "at": (t.updated_at or t.created_at).isoformat() if (t.updated_at or t.created_at) else None,
        })

    events = [e for e in events if e["at"]]
    events.sort(key=lambda e: e["at"], reverse=True)
    return {"items": events[:limit]}


@router.get("/predictions")
async def predictions():
    return {"revenue_forecast": [], "lead_forecast": [], "churn_risk": []}
