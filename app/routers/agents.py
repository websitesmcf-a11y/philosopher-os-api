from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.database.session import get_db
from app.core.security import get_current_user, get_current_org
from app.database.models import AgentMemory, Lead, Campaign, Client, Message, Invoice
from app.schemas.agent import MemoryEntry
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


def _agent_status_list(request: Request) -> list[dict]:
    council = request.app.state.council
    return [
        {
            "name": agent.name,
            "role": agent.role,
            "status": "idle",
            "tasks_completed": agent.tasks_completed,
            "tasks_failed": agent.tasks_failed,
        }
        for agent in council.agents.values()
    ]


@router.get("/")
async def list_agents(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """List all council agents."""
    return {"agents": _agent_status_list(request)}


@router.get("/status")
async def agents_status(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Return real agent status from the council orchestrator."""
    return {"agents": _agent_status_list(request)}


@router.get("/{agent_name}/memory")
async def get_agent_memory(
    agent_name: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Query recent AgentMemory entries for a specific agent."""
    stmt = (
        select(AgentMemory)
        .where(AgentMemory.agent_name == agent_name, AgentMemory.org_id == org_id)
        .order_by(AgentMemory.created_at.desc())
        .limit(20)
    )
    result = await db.execute(stmt)
    memories = result.scalars().all()
    return {
        "agent": agent_name,
        "memories": [
            {
                "id": str(m.id),
                "memory_type": m.memory_type,
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in memories
        ],
    }


@router.post("/{agent_name}/memory")
async def add_agent_memory(
    agent_name: str,
    entry: MemoryEntry,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Store a new memory entry for an agent."""
    memory = AgentMemory(
        org_id=org_id,
        agent_name=agent_name,
        memory_type=entry.memory_type or "note",
        content=entry.content,
        embedding=None,
    )
    db.add(memory)
    await db.commit()
    return {"agent": agent_name, "stored": True, "id": str(memory.id)}


@router.get("/plato/briefing")
@router.post("/plato/briefing")
async def get_morning_briefing(
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Generate a real morning briefing by aggregating org metrics."""
    lead_count = await db.scalar(
        select(func.count(Lead.id)).where(Lead.org_id == org_id)
    )
    active_campaigns = await db.scalar(
        select(func.count(Campaign.id)).where(Campaign.org_id == org_id, Campaign.status == "active")
    )
    client_count = await db.scalar(
        select(func.count(Client.id)).where(Client.org_id == org_id)
    )

    mrr_result = await db.execute(
        select(func.coalesce(func.sum(Client.mrr), 0)).where(Client.org_id == org_id)
    )
    total_mrr = mrr_result.scalar() or 0

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_msgs = await db.scalar(
        select(func.count(Message.id)).where(
            Message.direction == "inbound",
            Message.created_at >= today_start,
        )
    )

    return {
        "briefing": {
            "summary": (
                f"You have {lead_count or 0} leads, {client_count or 0} clients, "
                f"and {active_campaigns or 0} active campaigns. "
                f"Current MRR is ${total_mrr:,.2f}. "
                f"{today_msgs or 0} inbound messages received today."
            ),
            "metrics": {
                "leads": lead_count or 0,
                "clients": client_count or 0,
                "active_campaigns": active_campaigns or 0,
                "mrr": float(total_mrr),
                "messages_today": today_msgs or 0,
            },
            "recommendations": [],
            "alerts": [],
        }
    }
