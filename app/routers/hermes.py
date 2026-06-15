"""Hermes router — background job submission and polling."""
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.core.security import get_current_org, get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


class JobSubmitRequest(BaseModel):
    agent: str
    task: str


@router.post("/submit")
async def submit_job(
    req: JobSubmitRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Submit a task for background execution. Returns a job_id for polling."""
    hermes = getattr(request.app.state, 'hermes', None)
    if not hermes:
        raise HTTPException(status_code=500, detail="Hermes engine not available")

    result = hermes.submit_job(
        agent_name=req.agent,
        task=req.task,
        org_id=org_id,
        db_session=db,
    )
    return result


@router.get("/status/{job_id}")
async def get_job_status(
    job_id: str,
    request: Request,
):
    """Poll the status of a background job."""
    hermes = getattr(request.app.state, 'hermes', None)
    if not hermes:
        raise HTTPException(status_code=500, detail="Hermes engine not available")

    job = hermes.get_job_status(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/jobs")
async def list_jobs(
    request: Request,
    limit: int = 20,
):
    """List recent background jobs."""
    hermes = getattr(request.app.state, 'hermes', None)
    if not hermes:
        raise HTTPException(status_code=500, detail="Hermes engine not available")
    return {"jobs": hermes.list_jobs(limit=limit)}
