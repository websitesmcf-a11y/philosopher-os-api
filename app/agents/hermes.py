"""Hermes — autonomous background execution engine for long-running tasks."""
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any

from app.agents.base import BaseAgent, AgentContext

logger = logging.getLogger(__name__)


class JobStatus:
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class HermesAgent(BaseAgent):
    """Background execution engine. Manages async job queue for long-running tasks.

    Handles: lead discovery, web research, batch outreach, multi-step workflows.
    Max 3 concurrent background jobs.
    """

    def __init__(self):
        super().__init__(
            name="hermes",
            role="Autonomous Execution Engine",
            system_prompt=(
                "You are Hermes, the autonomous execution engine. You manage background jobs "
                "for long-running tasks like lead discovery, web research, and batch operations. "
                "You submit tasks to specialist agents and track their progress."
            ),
        )
        self._jobs: dict[str, dict] = {}
        self._running: set[str] = set()
        self._max_concurrent = 3
        self._semaphore = asyncio.Semaphore(self._max_concurrent)

    @property
    def tools(self) -> list[dict]:
        return []  # Hermes is invoked programmatically, not via LLM

    def submit_job(self, agent_name: str, task: str, org_id: str | None = None, db_session: Any = None) -> dict:
        """Submit a task for background execution. Returns immediately with a job_id."""
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = {
            "id": job_id,
            "agent": agent_name,
            "task": task,
            "status": JobStatus.QUEUED,
            "progress": None,
            "result": None,
            "error": None,
            "org_id": org_id,
            "created_at": datetime.utcnow().isoformat(),
            "started_at": None,
            "completed_at": None,
        }
        # Start execution in background
        asyncio.create_task(self._execute_job(job_id, agent_name, task, org_id, db_session))
        return {"job_id": job_id, "status": JobStatus.QUEUED}

    def adopt_job(self, agent_name: str, task: str, running: asyncio.Task, org_id: str | None = None) -> dict:
        """Track an already-running agent task as a background job.

        Used when a synchronous delegation outlives its budget: the work keeps
        running and its eventual result lands here instead of being discarded.
        """
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = {
            "id": job_id,
            "agent": agent_name,
            "task": task,
            "status": JobStatus.RUNNING,
            "progress": None,
            "result": None,
            "error": None,
            "org_id": org_id,
            "created_at": datetime.utcnow().isoformat(),
            "started_at": datetime.utcnow().isoformat(),
            "completed_at": None,
        }

        def _on_done(fut: asyncio.Task):
            job = self._jobs.get(job_id)
            if not job:
                return
            job["completed_at"] = datetime.utcnow().isoformat()
            try:
                result = fut.result()
                job["result"] = {
                    "message": result.message,
                    "success": result.success,
                    "data": str(result.data) if result.data else None,
                    "tool_calls": result.tool_calls,
                }
                job["status"] = JobStatus.COMPLETED
            except Exception as e:
                job["status"] = JobStatus.FAILED
                job["error"] = str(e)

        running.add_done_callback(_on_done)
        return {"job_id": job_id, "status": JobStatus.RUNNING}

    def get_job_status(self, job_id: str) -> dict | None:
        return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 20) -> list[dict]:
        jobs = sorted(self._jobs.values(), key=lambda j: j["created_at"], reverse=True)
        return jobs[:limit]

    async def _execute_job(self, job_id: str, agent_name: str, task: str, org_id: str | None, db_session: Any):
        """Execute a job by delegating to the appropriate agent in the background.

        Background tasks must use their own database session — never reuse the
        request-scoped session passed in from the router (it gets closed when
        the route handler returns).
        """
        from app.database.session import async_session

        async with self._semaphore:
            job = self._jobs.get(job_id)
            if not job:
                return
            job["status"] = JobStatus.RUNNING
            job["started_at"] = datetime.utcnow().isoformat()
            self._running.add(job_id)

            try:
                # Look up the agent from council (set when registered)
                council = getattr(self, 'council', None)
                if not council or agent_name not in council.agents:
                    raise ValueError(f"Agent '{agent_name}' not found in council")

                target = council.agents[agent_name]

                # Background job gets its own session (request-scoped session
                # is already closed by the time this task runs).
                async with async_session() as bg_session:
                    context = AgentContext(
                        user_input=task,
                        org_id=org_id,
                        db_session=bg_session,
                    )
                    result = await target.run(context)
                    await bg_session.commit()

                    job["result"] = {
                        "message": result.message,
                        "success": result.success,
                        "data": str(result.data) if result.data else None,
                        "tool_calls": result.tool_calls,
                    }
                    job["status"] = JobStatus.COMPLETED

            except Exception as e:
                logger.error(f"Hermes job {job_id} failed: {e}")
                job["status"] = JobStatus.FAILED
                job["error"] = str(e)
            finally:
                job["completed_at"] = datetime.utcnow().isoformat()
                self._running.discard(job_id)

    async def _execute_tool(self, tool_name: str, args: dict, context: AgentContext = None) -> Any:
        return {"status": "hermes_is_not_llm_driven"}
