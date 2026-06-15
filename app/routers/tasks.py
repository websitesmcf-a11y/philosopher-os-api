from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.session import get_db
from app.core.security import get_current_user, get_current_org
from app.schemas.task import TaskCreate, TaskUpdate, TaskListResponse
from app.services.task_service import TaskService

router = APIRouter()


@router.get("/", response_model=TaskListResponse)
async def list_tasks(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    assignee: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = TaskService(db, org_id=org_id)
    return await service.list_tasks(page=page, page_size=page_size, status=status, priority=priority, assignee_id=assignee)


@router.post("/", status_code=201)
async def create_task(
    data: TaskCreate,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = TaskService(db, org_id=org_id)
    return await service.create_task(data)


@router.patch("/{task_id}")
async def update_task(
    task_id: str,
    data: TaskUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = TaskService(db, org_id=org_id)
    return await service.update_task(task_id, data)


@router.post("/{task_id}/complete")
async def complete_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = TaskService(db, org_id=org_id)
    return await service.complete_task(task_id)


@router.delete("/{task_id}")
async def delete_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = TaskService(db, org_id=org_id)
    return await service.delete_task(task_id)


@router.get("/events")
async def task_events():
    """SSE stream for live task execution events."""
    from app.services.event_bus import event_stream
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
