"""Flows router — Strategeion visual automation builder CRUD + execution."""
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select, update, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Flow
from app.database.session import get_db
from app.core.security import get_current_org, get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Pydantic models ─────────────────────────────────────────────────

class FlowNode(BaseModel):
    id: str
    type: str  # trigger | philosopher | action | logic | omega
    position: dict  # { x, y }
    data: dict = {}  # node-specific config
    width: Optional[int] = 220
    height: Optional[int] = None

class FlowEdge(BaseModel):
    id: str
    source: str
    target: str
    sourceHandle: Optional[str] = None
    targetHandle: Optional[str] = None
    label: Optional[str] = None

class FlowData(BaseModel):
    nodes: list = []
    edges: list = []

class FlowCreate(BaseModel):
    name: str = "Untitled Flow"
    description: Optional[str] = None
    data: FlowData = FlowData()

class FlowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    data: Optional[FlowData] = None

class FlowRunRequest(BaseModel):
    node_id: Optional[str] = None  # Run from a specific node, or None = full flow


# ── CRUD endpoints ─────────────────────────────────────────────────

@router.get("/")
async def list_flows(
    org_id: str = Depends(get_current_org),
    db: AsyncSession = Depends(get_db),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
):
    """List all flows for the current org."""
    from sqlalchemy import func

    q = select(Flow).where(Flow.org_id == org_id)
    if status:
        q = q.where(Flow.status == status)
    q = q.order_by(desc(Flow.updated_at))

    # Count
    count_q = select(func.count()).select_from(Flow).where(Flow.org_id == org_id)
    if status:
        count_q = count_q.where(Flow.status == status)
    total = (await db.execute(count_q)).scalar() or 0

    # Paginate
    q = q.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(q)).scalars().all()

    return {
        "flows": [
            {
                "id": str(f.id),
                "name": f.name,
                "description": f.description,
                "status": f.status,
                "version": f.version,
                "run_count": f.run_count,
                "last_run_at": f.last_run_at.isoformat() if f.last_run_at else None,
                "last_run_status": f.last_run_status,
                "created_at": f.created_at.isoformat() if f.created_at else None,
                "updated_at": f.updated_at.isoformat() if f.updated_at else None,
                "node_count": len((f.data or {}).get("nodes", [])),
            }
            for f in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/")
async def create_flow(
    body: FlowCreate,
    org_id: str = Depends(get_current_org),
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new flow."""
    flow = Flow(
        org_id=org_id,
        name=body.name,
        description=body.description,
        data=body.data.model_dump() if body.data else {},
        created_by=user_id,
    )
    db.add(flow)
    await db.flush()
    await db.refresh(flow)
    return {
        "id": str(flow.id),
        "name": flow.name,
        "status": flow.status,
        "message": "Flow created",
    }


@router.get("/{flow_id}")
async def get_flow(
    flow_id: str,
    org_id: str = Depends(get_current_org),
    db: AsyncSession = Depends(get_db),
):
    """Get a single flow with full node graph data."""
    result = await db.execute(
        select(Flow).where(Flow.id == flow_id, Flow.org_id == org_id)
    )
    flow = result.scalar_one_or_none()
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")
    return {
        "id": str(flow.id),
        "name": flow.name,
        "description": flow.description,
        "status": flow.status,
        "data": flow.data or {"nodes": [], "edges": []},
        "version": flow.version,
        "run_count": flow.run_count,
        "last_run_at": flow.last_run_at.isoformat() if flow.last_run_at else None,
        "last_run_status": flow.last_run_status,
        "created_at": flow.created_at.isoformat() if flow.created_at else None,
        "updated_at": flow.updated_at.isoformat() if flow.updated_at else None,
    }


@router.put("/{flow_id}")
async def update_flow(
    flow_id: str,
    body: FlowUpdate,
    org_id: str = Depends(get_current_org),
    db: AsyncSession = Depends(get_db),
):
    """Update a flow — name, description, status, or node graph."""
    result = await db.execute(
        select(Flow).where(Flow.id == flow_id, Flow.org_id == org_id)
    )
    flow = result.scalar_one_or_none()
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")

    if body.name is not None:
        flow.name = body.name
    if body.description is not None:
        flow.description = body.description
    if body.status is not None:
        flow.status = body.status
    if body.data is not None:
        flow.data = body.data.model_dump()
        flow.version = (flow.version or 0) + 1

    await db.flush()
    return {"id": str(flow.id), "message": "Flow updated"}


@router.delete("/{flow_id}")
async def delete_flow(
    flow_id: str,
    org_id: str = Depends(get_current_org),
    db: AsyncSession = Depends(get_db),
):
    """Delete a flow."""
    result = await db.execute(
        select(Flow).where(Flow.id == flow_id, Flow.org_id == org_id)
    )
    flow = result.scalar_one_or_none()
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")
    await db.delete(flow)
    return {"message": "Flow deleted"}


@router.post("/{flow_id}/run")
async def run_flow(
    flow_id: str,
    body: FlowRunRequest,
    request: Request,
    org_id: str = Depends(get_current_org),
    db: AsyncSession = Depends(get_db),
):
    """Execute a flow via Hermes. Walks the node graph and submits each agent node as a job."""
    hermes = getattr(request.app.state, "hermes", None)
    if not hermes:
        raise HTTPException(status_code=500, detail="Hermes engine not available")

    result = await db.execute(
        select(Flow).where(Flow.id == flow_id, Flow.org_id == org_id)
    )
    flow = result.scalar_one_or_none()
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")

    data = flow.data or {}
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    if not nodes:
        raise HTTPException(status_code=400, detail="Flow has no nodes")

    # Mark flow as running
    flow.status = "running"
    flow.last_run_status = "running"
    await db.flush()

    # Find trigger/starting nodes and submit them
    trigger_nodes = [n for n in nodes if n.get("type") in ("trigger",)]
    if not trigger_nodes:
        # Fall back to first node
        trigger_nodes = [nodes[0]]

    job_ids = []
    for tn in trigger_nodes:
        job = hermes.submit_job(
            agent_name="hermes",
            task=f"Execute flow: {flow.name}",
            task_type="flow",
            source="strategeion",
            org_id=org_id,
            input_data={
                "flow_id": str(flow.id),
                "flow_name": flow.name,
                "node_id": tn.get("id"),
                "data": tn.get("data", {}),
                "nodes": nodes,
                "edges": edges,
            },
        )
        job_ids.append(str(job["id"]))

    return {
        "message": f"Flow execution started with {len(job_ids)} trigger(s)",
        "flow_id": str(flow.id),
        "job_ids": job_ids,
    }


@router.post("/{flow_id}/duplicate")
async def duplicate_flow(
    flow_id: str,
    org_id: str = Depends(get_current_org),
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Duplicate an existing flow."""
    result = await db.execute(
        select(Flow).where(Flow.id == flow_id, Flow.org_id == org_id)
    )
    original = result.scalar_one_or_none()
    if not original:
        raise HTTPException(status_code=404, detail="Flow not found")

    duplicate = Flow(
        org_id=org_id,
        name=f"{original.name} (copy)",
        description=original.description,
        status="draft",
        data=original.data,
        version=1,
        created_by=user_id,
    )
    db.add(duplicate)
    await db.flush()
    await db.refresh(duplicate)
    return {"id": str(duplicate.id), "name": duplicate.name, "message": "Flow duplicated"}


# ── Smart Sequence ─────────────────────────────────────────────

class SmartSequenceRequest(BaseModel):
    prompt: str

@router.post("/smart-sequence")
async def smart_sequence(
    body: SmartSequenceRequest,
    request: Request,
    org_id: str = Depends(get_current_org),
):
    """Generate a flow from a natural language description using AI."""
    from app.llm.client import llm

    system = """You are a flow architect for the Philosopher OS automation builder (Strategeion).
Generate a node-based automation flow from the user's description.

Available node types and agents:

TRIGGERS: New Lead Added, WhatsApp Message, Email Received, Calendar Event, Scheduled (Cron), Webhook, Manual Run
PHILOSOPHERS: Plato (Strategy), Socrates (Questioning), Aristotle (Logic), Athena (Tactics), Heraclitus (Change),
  Pythagoras (Metrics), Solon (Governance), Leonidas (Action), Archimedes (Building), Odysseus (Navigation)
GODS: Iapetus (Workflows), Astraeus (Intel), Erebos (Cleanup), Phantasos (Creative), Stilbon (Messaging)
OMEGA: Genesis (Creation), Overmind (Conquest), Omniscient (Truth), Eternal (Time), Singularity (Unity)
ACTIONS: Send WhatsApp, Send Email, Post to Facebook, Post to Instagram, Update Lead, Create Task, Notify Team
LOGIC: If/Else Condition, Time Delay, Wait for Reply, Loop, Stop Flow

Return ONLY valid JSON (no markdown, no backticks):
{
  "nodes": [
    {
      "id": "n-1",
      "type": "trigger",
      "position": {"x": 55, "y": 115},
      "data": {
        "label": "NEW LEAD ADDED",
        "category": "trigger",
        "subtitle": "When a new lead enters the CRM",
        "agentColor": "#1A5088",
        "agentName": "New Lead",
        "agentInitial": "NL",
        "config": {},
        "status": "idle"
      }
    }
  ],
  "edges": [
    {"id": "e-1", "source": "n-1", "target": "n-2"}
  ],
  "suggestion": "A brief explanation of the flow"
}

Position nodes in a left-to-right layout starting at x=55,y=115 with 275px horizontal spacing between columns."""

    user_msg = f"Create a flow for: {body.prompt}"

    result = await llm.generate(
        system=system,
        messages=[{"role": "user", "content": user_msg}],
        model="deepseek-v4-flash",
        temperature=0.3,
        max_tokens=4096,
    )

    import json, re
    text = result.text.strip()
    # Strip markdown fence if present
    if text.startswith("```"):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
    try:
        flow_data = json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON block within the response
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            flow_data = json.loads(match.group())
        else:
            raise HTTPException(status_code=500, detail=f"LLM returned invalid JSON: {text[:500]}")

    nodes = flow_data.get("nodes", [])
    edges = flow_data.get("edges", [])
    suggestion = flow_data.get("suggestion", "")

    if not nodes:
        raise HTTPException(status_code=500, detail="No nodes generated — try a more specific prompt")

    return {
        "nodes": nodes,
        "edges": edges,
        "suggestion": suggestion,
        "node_count": len(nodes),
        "edge_count": len(edges),
    }
