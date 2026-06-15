"""Task executor — runs scheduled tasks at their specified time.

Integrated into the JobScheduler polling loop. When a task's due_date
arrives, the executor runs it (optionally via a council agent) and
broadcasts live events over SSE.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Task
from app.database.session import async_session
# SSE pub/sub now lives in the shared event bus so that task executions and
# interactive agent runs (chat / Beast Mode) share one live stream.
from app.services.event_bus import broadcast as _broadcast  # noqa: F401
from app.services.event_bus import event_stream, subscribe, unsubscribe  # noqa: F401

logger = logging.getLogger(__name__)


# ─── Task execution ─────────────────────────────────────────────
async def execute_task(task: Task, db: AsyncSession) -> dict:
    """Execute a single task. If assigned_agent is set, delegates to council."""
    task.status = "in_progress"
    await db.flush()

    _broadcast({
        "type": "task_started",
        "task_id": str(task.id),
        "title": task.title,
        "scheduled_at": task.due_date.isoformat() if task.due_date else None,
    })

    try:
        council = _get_council()
        result_msg = ""
        success = True

        if task.assigned_agent and council:
            agent = council.agents.get(task.assigned_agent.lower())
            if agent:
                from app.agents.base import AgentContext
                context = AgentContext(
                    user_input=task.description or task.title,
                    org_id=str(task.org_id) if task.org_id else None,
                    db_session=db,
                )
                result = await agent.run(context)
                result_msg = result.message or ""
                success = result.success
            else:
                result_msg = f"Agent '{task.assigned_agent}' not found"
                success = False
        else:
            result_msg = "Task completed (no agent assigned)"
            success = True

        task.status = "completed"
        task.completed_at = datetime.now(timezone.utc)

        _broadcast({
            "type": "task_completed" if success else "task_failed",
            "task_id": str(task.id),
            "title": task.title,
            "result": (result_msg or "")[:300],
        })

        return {"status": "completed" if success else "failed", "message": (result_msg or "")[:200]}

    except Exception as e:
        task.status = "pending"
        logger.error(f"Task {task.id} execution failed: {e}")

        _broadcast({
            "type": "task_failed",
            "task_id": str(task.id),
            "title": task.title,
            "error": str(e),
        })

        return {"status": "failed", "error": str(e)}


async def check_due_tasks(db: AsyncSession) -> int:
    """Find and execute all pending tasks past their due_date.

    due_date values may be stored either timezone-aware or naive (SQLite stores
    DateTime as text without an offset). To avoid fragile DB-level comparisons
    between naive and aware datetimes, we fetch pending tasks with a due_date
    and decide which are due in Python, normalizing every value to UTC.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(Task).where(
            Task.status == "pending",
            Task.due_date.isnot(None),
        ).limit(50)
    )
    tasks = list(result.scalars().all())
    executed = 0
    for task in tasks:
        due = task.due_date
        if due is None:
            continue
        # Treat naive datetimes as UTC wall-clock for comparison.
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due > now:
            continue
        try:
            await execute_task(task, db)
            executed += 1
        except Exception as e:
            logger.error(f"Failed to execute task {task.id}: {e}")
    return executed


def _get_council():
    try:
        from app.main import council
        return council
    except Exception:
        return None
