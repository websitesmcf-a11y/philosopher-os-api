"""Hermes router — persistent background job management."""
import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select, update, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.core.security import get_current_org, get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_hermes(request: Request):
    hermes = getattr(request.app.state, "hermes", None)
    if not hermes:
        raise HTTPException(status_code=500, detail="Hermes engine not available")
    return hermes


# ── Request models ───────────────────────────────────────────────────────

class JobSubmitRequest(BaseModel):
    agent: str
    task: str
    task_type: str = "general"
    source: str = "api"
    input: dict = {}
    priority: int = 5
    max_attempts: int = 2
    scheduled_for: Optional[str] = None
    mission_id: Optional[str] = None
    parent_job_id: Optional[str] = None


# ── Submit endpoints (new + legacy) ─────────────────────────────────────

@router.post("/jobs")
async def submit_job_new(
    req: JobSubmitRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Submit a background job. Returns immediately with job_id."""
    hermes = _get_hermes(request)
    scheduled = None
    if req.scheduled_for:
        try:
            scheduled = datetime.fromisoformat(req.scheduled_for)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid scheduled_for format (use ISO 8601)")

    result = hermes.submit_job(
        agent_name=req.agent,
        task=req.task,
        org_id=org_id,
        task_type=req.task_type,
        source=req.source,
        input_data=req.input,
        max_attempts=req.max_attempts,
        priority=req.priority,
        mission_id=req.mission_id,
        parent_job_id=req.parent_job_id,
        scheduled_for=scheduled,
    )
    return result


@router.post("/submit")
async def submit_job_legacy(
    req: JobSubmitRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Legacy submit endpoint — kept for backward compatibility."""
    return await submit_job_new(req, request, db, org_id, user)


# ── List + filter ────────────────────────────────────────────────────────

@router.get("/jobs")
async def list_jobs(
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    limit: int = Query(50, le=200),
    status: Optional[str] = None,
    agent: Optional[str] = None,
    source: Optional[str] = None,
    mission_id: Optional[str] = None,
):
    """List Hermes jobs. Combines in-memory cache with DB for full history."""
    hermes = _get_hermes(request)

    # In-memory (recent) jobs
    mem_jobs = hermes.list_jobs(limit=limit, status=status, agent=agent,
                                source=source, org_id=org_id)

    # If memory doesn't have enough, supplement from DB
    if len(mem_jobs) < 20:
        from app.database.models import HermesJob
        q = select(HermesJob).where(HermesJob.org_id == uuid.UUID(org_id))
        if status:
            q = q.where(HermesJob.status == status)
        if agent:
            q = q.where(HermesJob.agent_name == agent)
        if source:
            q = q.where(HermesJob.source == source)
        if mission_id:
            q = q.where(HermesJob.mission_id == mission_id)
        q = q.order_by(desc(HermesJob.created_at)).limit(limit)
        result = await db.execute(q)
        db_jobs = [hermes._row_to_dict(r) for r in result.scalars()]

        # Merge: memory wins for same job_id
        mem_ids = {j["id"] for j in mem_jobs}
        merged = mem_jobs + [j for j in db_jobs if j["id"] not in mem_ids]
        return {"jobs": sorted(merged, key=lambda j: j.get("created_at", ""), reverse=True)[:limit]}

    return {"jobs": mem_jobs}


# ── Job detail ───────────────────────────────────────────────────────────

@router.get("/jobs/{job_id}")
async def get_job_detail(
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
):
    """Full job detail including logs."""
    hermes = _get_hermes(request)

    job = hermes.get_job_status(job_id)
    if not job:
        job = await hermes._load_job_from_db(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Load logs from DB
    from app.database.models import HermesJobLog
    log_result = await db.execute(
        select(HermesJobLog)
        .where(HermesJobLog.job_id == uuid.UUID(job_id))
        .order_by(HermesJobLog.created_at.asc())
        .limit(500)
    )
    logs = [
        {
            "id": str(r.id),
            "level": r.level,
            "message": r.message,
            "metadata": r.extra_metadata,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in log_result.scalars()
    ]

    # Load child jobs (sub-jobs)
    from app.database.models import HermesJob
    children_result = await db.execute(
        select(HermesJob).where(HermesJob.parent_job_id == uuid.UUID(job_id))
        .order_by(HermesJob.created_at.asc())
    )
    children = [hermes._row_to_dict(r) for r in children_result.scalars()]

    return {**job, "logs": logs, "children": children}


# ── Status (legacy polling endpoint) ────────────────────────────────────

@router.get("/status/{job_id}")
async def get_job_status(job_id: str, request: Request):
    hermes = _get_hermes(request)
    job = hermes.get_job_status(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ── Cancel ───────────────────────────────────────────────────────────────

@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    request: Request,
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    hermes = _get_hermes(request)
    result = await hermes.cancel_job(job_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ── Retry ────────────────────────────────────────────────────────────────

@router.post("/jobs/{job_id}/retry")
async def retry_job(
    job_id: str,
    request: Request,
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    hermes = _get_hermes(request)
    result = await hermes.retry_job(job_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ── Logs ─────────────────────────────────────────────────────────────────

@router.get("/jobs/{job_id}/logs")
async def get_job_logs(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    limit: int = Query(500, le=1000),
    level: Optional[str] = None,
):
    from app.database.models import HermesJobLog
    q = select(HermesJobLog).where(HermesJobLog.job_id == uuid.UUID(job_id))
    if level:
        q = q.where(HermesJobLog.level == level)
    q = q.order_by(HermesJobLog.created_at.asc()).limit(limit)
    result = await db.execute(q)
    logs = [
        {
            "id": str(r.id),
            "level": r.level,
            "message": r.message,
            "metadata": r.extra_metadata,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in result.scalars()
    ]
    return {"job_id": job_id, "logs": logs, "count": len(logs)}


# ── Health ───────────────────────────────────────────────────────────────

@router.get("/health")
async def hermes_health(request: Request, db: AsyncSession = Depends(get_db)):
    hermes = _get_hermes(request)
    all_jobs = hermes.list_jobs(limit=1000)
    running = [j for j in all_jobs if j["status"] == "running"]
    queued = [j for j in all_jobs if j["status"] == "queued"]
    failed = [j for j in all_jobs if j["status"] == "failed"]

    # Quick DB ping
    db_ok = False
    try:
        from sqlalchemy import text
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass

    return {
        "status": "healthy",
        "max_concurrent": hermes._max_concurrent,
        "running_jobs": len(running),
        "queued_jobs": len(queued),
        "failed_jobs": len(failed),
        "total_in_memory": len(hermes._jobs),
        "database_connected": db_ok,
        "semaphore_available": hermes._semaphore._value,
    }
