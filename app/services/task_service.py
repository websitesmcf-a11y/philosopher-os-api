import uuid
import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete as sa_delete
from app.database.models import Task
from app.schemas.task import TaskCreate, TaskUpdate

logger = logging.getLogger(__name__)


class TaskService:
    def __init__(self, db: AsyncSession, org_id: str):
        self.db = db
        self.org_id = org_id

    async def list_tasks(self, page: int = 1, page_size: int = 20, **filters):
        query = select(Task).where(Task.org_id == self.org_id)
        if filters.get("status"):
            query = query.where(Task.status == filters["status"])
        if filters.get("priority"):
            query = query.where(Task.priority == filters["priority"])
        if filters.get("assignee_id"):
            query = query.where(Task.assignee_id == filters["assignee_id"])

        count_q = select(func.count()).select_from(query.subquery())
        total = (await self.db.execute(count_q)).scalar() or 0

        query = query.order_by(Task.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        items = result.scalars().all()

        return {
            "items": [self._to_response(t) for t in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def create_task(self, data: TaskCreate):
        task = Task(
            id=uuid.uuid4(),
            org_id=uuid.UUID(self.org_id) if isinstance(self.org_id, str) else self.org_id,
            **data.model_dump(exclude_none=True),
        )
        self.db.add(task)
        await self.db.flush()
        return self._to_response(task)

    async def get_task(self, task_id: str):
        result = await self.db.execute(
            select(Task).where(Task.id == task_id, Task.org_id == self.org_id)
        )
        task = result.scalar_one_or_none()
        if not task:
            from app.core.errors import NotFoundError
            raise NotFoundError("Task not found")
        return self._to_response(task)

    async def update_task(self, task_id: str, data: TaskUpdate):
        result = await self.db.execute(
            select(Task).where(Task.id == task_id, Task.org_id == self.org_id)
        )
        task = result.scalar_one_or_none()
        if not task:
            from app.core.errors import NotFoundError
            raise NotFoundError("Task not found")
        for key, val in data.model_dump(exclude_none=True).items():
            setattr(task, key, val)
        if data.status == "completed" and not task.completed_at:
            from datetime import datetime
            task.completed_at = datetime.utcnow()
        await self.db.flush()
        return self._to_response(task)

    async def complete_task(self, task_id: str):
        return await self.update_task(task_id, TaskUpdate(status="completed"))

    async def delete_task(self, task_id: str):
        result = await self.db.execute(
            select(Task).where(Task.id == task_id, Task.org_id == self.org_id)
        )
        task = result.scalar_one_or_none()
        if not task:
            from app.core.errors import NotFoundError
            raise NotFoundError("Task not found")
        await self.db.delete(task)

    def _to_response(self, task: Task):
        return {
            "id": str(task.id),
            "org_id": str(task.org_id),
            "title": task.title,
            "description": task.description,
            "assignee_id": str(task.assignee_id) if task.assignee_id else None,
            "assigned_agent": task.assigned_agent,
            "priority": task.priority,
            "status": task.status,
            "due_date": task.due_date,
            "completed_at": task.completed_at,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        }
