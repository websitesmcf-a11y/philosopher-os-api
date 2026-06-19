"""Hermes — persistent background job dispatcher for Philosopher OS.

Jobs are stored in the hermes_jobs table so they survive server restarts.
An in-memory cache mirrors DB state for fast polling; DB is the source of truth.

Lifecycle:
  submit_job() → writes DB row (queued) → fires asyncio task
  _execute_job() → updates DB at every state change
  update_progress() → writes progress to DB + cache (callable by agents)
  add_log() → writes to hermes_job_logs
  recover_jobs() → called at startup to reset stale "running" jobs
"""
import asyncio
import logging
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
    CANCELLED = "cancelled"
    RETRYING = "retrying"


# Default max concurrent jobs (configurable via settings)
DEFAULT_MAX_CONCURRENT = 3


class HermesAgent(BaseAgent):
    """Persistent background job dispatcher.

    Manages an async job queue backed by the hermes_jobs database table.
    Max concurrent jobs is configurable; default is 3.
    """

    def __init__(self):
        super().__init__(
            name="hermes",
            role="Background Job Dispatcher",
            system_prompt=(
                "You are Hermes, the background job dispatcher. "
                "You manage persistent async jobs for all Philosopher OS agents."
            ),
        )
        self._max_concurrent = DEFAULT_MAX_CONCURRENT
        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        # In-memory cache: fast read path. DB is the source of truth.
        self._jobs: dict[str, dict] = {}

    @property
    def tools(self) -> list[dict]:
        return []  # Hermes is not LLM-driven

    # ── DB helpers ──────────────────────────────────────────────────────

    async def _db_create_job(self, job_id: str, agent_name: str, task: str,
                             org_id: str, task_type: str, source: str,
                             input_data: dict, max_attempts: int,
                             priority: int, mission_id: str | None,
                             parent_job_id: str | None,
                             scheduled_for: datetime | None) -> None:
        from app.database.session import async_session
        from app.database.models import HermesJob
        try:
            async with async_session() as db:
                row = HermesJob(
                    id=uuid.UUID(job_id),
                    org_id=uuid.UUID(org_id) if org_id else uuid.UUID("00000000-0000-0000-0000-000000000001"),
                    agent_name=agent_name,
                    task=task,
                    task_type=task_type,
                    source=source,
                    input_data=input_data or {},
                    status=JobStatus.QUEUED,
                    max_attempts=max_attempts,
                    priority=priority,
                    mission_id=mission_id,
                    parent_job_id=uuid.UUID(parent_job_id) if parent_job_id else None,
                    scheduled_for=scheduled_for,
                )
                db.add(row)
                await db.commit()
        except Exception as e:
            logger.error("Hermes: failed to persist job %s to DB: %s", job_id, e)

    async def _db_update(self, job_id: str, **fields) -> None:
        from app.database.session import async_session
        from app.database.models import HermesJob
        from sqlalchemy import update
        try:
            async with async_session() as db:
                fields["updated_at"] = datetime.utcnow()
                await db.execute(
                    update(HermesJob)
                    .where(HermesJob.id == uuid.UUID(job_id))
                    .values(**fields)
                )
                await db.commit()
        except Exception as e:
            logger.warning("Hermes: DB update failed for %s: %s", job_id, e)

    async def _db_add_log(self, job_id: str, message: str, level: str = "info",
                          org_id: str | None = None, metadata: dict | None = None) -> None:
        from app.database.session import async_session
        from app.database.models import HermesJobLog
        try:
            async with async_session() as db:
                row = HermesJobLog(
                    job_id=uuid.UUID(job_id),
                    org_id=uuid.UUID(org_id) if org_id else None,
                    level=level,
                    message=message,
                    extra_metadata=metadata or {},
                )
                db.add(row)
                await db.commit()
        except Exception as e:
            logger.warning("Hermes: log write failed for %s: %s", job_id, e)

    # ── Public API ───────────────────────────────────────────────────────

    def submit_job(
        self,
        agent_name: str,
        task: str,
        org_id: str | None = None,
        db_session: Any = None,
        task_type: str = "general",
        source: str = "api",
        input_data: dict | None = None,
        max_attempts: int = 2,
        priority: int = 5,
        mission_id: str | None = None,
        parent_job_id: str | None = None,
        scheduled_for: datetime | None = None,
    ) -> dict:
        """Submit a task for background execution. Returns immediately with a job_id."""
        job_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        self._jobs[job_id] = {
            "id": job_id,
            "agent": agent_name,
            "task": task,
            "task_type": task_type,
            "source": source,
            "status": JobStatus.QUEUED,
            "progress_percent": 0,
            "progress_message": None,
            "current_step": None,
            "completed_steps": 0,
            "total_steps": None,
            "result": None,
            "error": None,
            "org_id": org_id,
            "mission_id": mission_id,
            "parent_job_id": parent_job_id,
            "attempt_count": 0,
            "max_attempts": max_attempts,
            "created_at": now,
            "started_at": None,
            "completed_at": None,
        }

        # Persist to DB (fire-and-forget, don't block submit)
        asyncio.create_task(self._db_create_job(
            job_id, agent_name, task, org_id or "",
            task_type, source, input_data or {}, max_attempts, priority,
            mission_id, parent_job_id, scheduled_for,
        ))

        asyncio.create_task(self._execute_job(job_id, agent_name, task, org_id))
        logger.info("Hermes: submitted job %s → %s", job_id, agent_name)
        return {"job_id": job_id, "status": JobStatus.QUEUED}

    async def update_progress(
        self,
        job_id: str,
        progress_percent: int,
        progress_message: str | None = None,
        current_step: str | None = None,
        completed_steps: int | None = None,
        total_steps: int | None = None,
    ) -> None:
        """Called by agents to report progress. Updates DB + in-memory cache."""
        if job_id in self._jobs:
            self._jobs[job_id]["progress_percent"] = progress_percent
            self._jobs[job_id]["progress_message"] = progress_message
            if current_step is not None:
                self._jobs[job_id]["current_step"] = current_step
            if completed_steps is not None:
                self._jobs[job_id]["completed_steps"] = completed_steps
            if total_steps is not None:
                self._jobs[job_id]["total_steps"] = total_steps

        fields: dict = {"progress_percent": progress_percent, "status": JobStatus.RUNNING}
        if progress_message:
            fields["progress_message"] = progress_message
        if current_step:
            fields["current_step"] = current_step
        if completed_steps is not None:
            fields["completed_steps"] = completed_steps
        if total_steps is not None:
            fields["total_steps"] = total_steps

        await self._db_update(job_id, **fields)

    async def add_log(self, job_id: str, message: str, level: str = "info",
                      org_id: str | None = None, metadata: dict | None = None) -> None:
        """Write a structured log entry for this job."""
        logger.log(
            {"debug": 10, "info": 20, "warning": 30, "error": 40, "success": 20}.get(level, 20),
            "Hermes[%s] %s: %s", job_id[:8], level.upper(), message,
        )
        await self._db_add_log(job_id, message, level, org_id, metadata)

    async def cancel_job(self, job_id: str) -> dict:
        """Request cancellation of a queued or running job."""
        job = self._jobs.get(job_id)
        if not job:
            return {"error": "Job not found"}
        if job["status"] in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            return {"error": f"Job already in terminal state: {job['status']}"}

        job["status"] = JobStatus.CANCELLED
        job["completed_at"] = datetime.utcnow().isoformat()
        await self._db_update(
            job_id,
            status=JobStatus.CANCELLED,
            cancellation_requested=True,
            cancelled_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
        )
        await self.add_log(job_id, "Job cancelled by user", "warning", job.get("org_id"))
        return {"job_id": job_id, "status": JobStatus.CANCELLED}

    async def retry_job(self, job_id: str) -> dict:
        """Create a new attempt for a failed job."""
        job = self._jobs.get(job_id)
        if not job:
            # Try to load from DB
            job = await self._load_job_from_db(job_id)
        if not job:
            return {"error": "Job not found"}
        if job["status"] == JobStatus.RUNNING:
            return {"error": "Job is already running"}

        job["status"] = JobStatus.QUEUED
        job["progress_percent"] = 0
        job["progress_message"] = "Retrying..."
        job["error"] = None
        job["attempt_count"] = job.get("attempt_count", 0) + 1
        job["started_at"] = None
        job["completed_at"] = None
        self._jobs[job_id] = job

        await self._db_update(
            job_id,
            status=JobStatus.RETRYING,
            attempt_count=job["attempt_count"],
            error_message=None,
            progress_percent=0,
        )
        await self.add_log(job_id, f"Manual retry (attempt {job['attempt_count']})", "info", job.get("org_id"))
        asyncio.create_task(self._execute_job(job_id, job["agent"], job["task"], job.get("org_id")))
        return {"job_id": job_id, "status": JobStatus.QUEUED, "attempt": job["attempt_count"]}

    def get_job_status(self, job_id: str) -> dict | None:
        return self._jobs.get(job_id)

    def list_jobs(
        self,
        limit: int = 50,
        status: str | None = None,
        agent: str | None = None,
        source: str | None = None,
        org_id: str | None = None,
    ) -> list[dict]:
        jobs = list(self._jobs.values())
        if status:
            jobs = [j for j in jobs if j["status"] == status]
        if agent:
            jobs = [j for j in jobs if j["agent"] == agent]
        if source:
            jobs = [j for j in jobs if j.get("source") == source]
        if org_id:
            jobs = [j for j in jobs if j.get("org_id") == org_id]
        return sorted(jobs, key=lambda j: j["created_at"], reverse=True)[:limit]

    # ── Internal execution ───────────────────────────────────────────────

    async def _execute_job(self, job_id: str, agent_name: str, task: str, org_id: str | None):
        from app.database.session import async_session

        async with self._semaphore:
            job = self._jobs.get(job_id)
            if not job:
                return
            if job.get("status") == JobStatus.CANCELLED:
                return

            # Mark running
            job["status"] = JobStatus.RUNNING
            job["started_at"] = datetime.utcnow().isoformat()
            job["progress_percent"] = 5
            job["progress_message"] = "Starting..."
            await self._db_update(
                job_id,
                status=JobStatus.RUNNING,
                started_at=datetime.utcnow(),
                progress_percent=5,
                progress_message="Starting...",
                attempt_count=job.get("attempt_count", 0) + 1,
            )
            await self.add_log(job_id, f"Job started → agent: {agent_name}", "info", org_id)

            try:
                council = getattr(self, "council", None)
                if not council or agent_name not in council.agents:
                    raise ValueError(f"Agent '{agent_name}' not found")

                target = council.agents[agent_name]

                await self.update_progress(job_id, 20, "Agent initialising...")

                async with async_session() as bg_session:
                    context = AgentContext(
                        user_input=task,
                        org_id=org_id,
                        db_session=bg_session,
                    )
                    # Give agents access to Hermes for progress reporting
                    context.hermes = self
                    context.hermes_job_id = job_id

                    await self.update_progress(job_id, 40, "Running agent...")
                    result = await target.run(context)
                    await bg_session.commit()

                await self.update_progress(job_id, 90, "Saving results...")

                output = {
                    "message": result.message,
                    "success": result.success,
                    "data": str(result.data)[:2000] if result.data else None,
                    "tool_calls": result.tool_calls,
                }
                job["result"] = output
                job["status"] = JobStatus.COMPLETED
                job["progress_percent"] = 100
                job["progress_message"] = "Completed"
                job["completed_at"] = datetime.utcnow().isoformat()

                await self._db_update(
                    job_id,
                    status=JobStatus.COMPLETED,
                    progress_percent=100,
                    progress_message="Completed",
                    output_data=output,
                    completed_at=datetime.utcnow(),
                )
                await self.add_log(job_id, "Job completed successfully", "success", org_id)
                await self._notify_completion(job_id, job, org_id)

            except asyncio.CancelledError:
                job["status"] = JobStatus.CANCELLED
                job["completed_at"] = datetime.utcnow().isoformat()
                await self._db_update(job_id, status=JobStatus.CANCELLED, completed_at=datetime.utcnow())
                await self.add_log(job_id, "Job cancelled (asyncio)", "warning", org_id)

            except Exception as e:
                err_str = str(e)
                logger.error("Hermes job %s failed: %s", job_id, err_str)
                job["error"] = err_str
                job["completed_at"] = datetime.utcnow().isoformat()

                attempt = job.get("attempt_count", 1)
                max_att = job.get("max_attempts", 2)

                if attempt < max_att and not self._is_non_retryable(err_str):
                    # Auto-retry
                    job["status"] = JobStatus.RETRYING
                    job["progress_percent"] = 0
                    job["progress_message"] = f"Retrying (attempt {attempt + 1}/{max_att})..."
                    await self._db_update(
                        job_id,
                        status=JobStatus.RETRYING,
                        error_message=err_str,
                        attempt_count=attempt,
                    )
                    await self.add_log(job_id, f"Auto-retry {attempt + 1}/{max_att}: {err_str}", "warning", org_id)
                    await asyncio.sleep(10 * attempt)  # backoff
                    job["attempt_count"] = attempt + 1
                    asyncio.create_task(self._execute_job(job_id, agent_name, task, org_id))
                else:
                    job["status"] = JobStatus.FAILED
                    await self._db_update(
                        job_id,
                        status=JobStatus.FAILED,
                        error_message=err_str,
                        completed_at=datetime.utcnow(),
                        progress_message=f"Failed: {err_str[:200]}",
                    )
                    await self.add_log(job_id, f"Job failed: {err_str}", "error", org_id)
                    await self._notify_failure(job_id, job, org_id, err_str)

    def _is_non_retryable(self, error: str) -> bool:
        """Return True for errors where retrying won't help."""
        non_retryable = [
            "api key", "invalid credentials", "permission denied",
            "not found", "validation", "cancelled by user",
        ]
        err_lower = error.lower()
        return any(kw in err_lower for kw in non_retryable)

    async def _notify_completion(self, job_id: str, job: dict, org_id: str | None) -> None:
        try:
            from app.database.session import async_session
            from app.services.notification_service import send_notification
            async with async_session() as db:
                await send_notification(
                    db=db,
                    title="Hermes job completed",
                    body=f"{job['agent']} finished: {job['task'][:80]}",
                    notification_type="hermes_completed",
                    org_id=org_id or "00000000-0000-0000-0000-000000000001",
                    extra_data={"job_id": job_id},
                )
        except Exception as e:
            logger.warning("Hermes: notification failed for %s: %s", job_id, e)

    async def _notify_failure(self, job_id: str, job: dict, org_id: str | None, error: str) -> None:
        try:
            from app.database.session import async_session
            from app.services.notification_service import send_notification
            async with async_session() as db:
                await send_notification(
                    db=db,
                    title="Hermes job failed",
                    body=f"{job['agent']} failed: {error[:120]}",
                    notification_type="hermes_failed",
                    org_id=org_id or "00000000-0000-0000-0000-000000000001",
                    extra_data={"job_id": job_id, "error": error},
                )
        except Exception as e:
            logger.warning("Hermes: failure notification failed for %s: %s", job_id, e)

    async def _load_job_from_db(self, job_id: str) -> dict | None:
        from app.database.session import async_session
        from app.database.models import HermesJob
        from sqlalchemy import select
        try:
            async with async_session() as db:
                result = await db.execute(
                    select(HermesJob).where(HermesJob.id == uuid.UUID(job_id))
                )
                row = result.scalar_one_or_none()
                if not row:
                    return None
                return self._row_to_dict(row)
        except Exception:
            return None

    def _row_to_dict(self, row) -> dict:
        return {
            "id": str(row.id),
            "agent": row.agent_name,
            "task": row.task,
            "task_type": row.task_type,
            "source": row.source,
            "status": row.status,
            "progress_percent": row.progress_percent,
            "progress_message": row.progress_message,
            "current_step": row.current_step,
            "completed_steps": row.completed_steps,
            "total_steps": row.total_steps,
            "result": row.output_data,
            "error": row.error_message,
            "org_id": str(row.org_id) if row.org_id else None,
            "mission_id": row.mission_id,
            "parent_job_id": str(row.parent_job_id) if row.parent_job_id else None,
            "attempt_count": row.attempt_count,
            "max_attempts": row.max_attempts,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }

    # ── Startup recovery ────────────────────────────────────────────────

    async def recover_jobs(self) -> None:
        """Called at startup. Reset stale 'running' jobs to 'failed'.

        Jobs marked running but with no in-memory task are orphaned — the
        server crashed mid-execution. Mark them failed and add a log entry.
        """
        from app.database.session import async_session
        from app.database.models import HermesJob, HermesJobLog
        from sqlalchemy import select, update

        try:
            async with async_session() as db:
                result = await db.execute(
                    select(HermesJob).where(HermesJob.status.in_(["running", "retrying"]))
                )
                stale = result.scalars().all()
                for row in stale:
                    job_id = str(row.id)
                    await db.execute(
                        update(HermesJob)
                        .where(HermesJob.id == row.id)
                        .values(
                            status=JobStatus.FAILED,
                            error_message="Job interrupted — server restarted mid-execution",
                            completed_at=datetime.utcnow(),
                        )
                    )
                    db.add(HermesJobLog(
                        job_id=row.id,
                        org_id=row.org_id,
                        level="warning",
                        message="Job recovered after server restart — marked failed",
                    ))
                    # Load into memory cache so it's visible
                    row.status = JobStatus.FAILED
                    self._jobs[job_id] = self._row_to_dict(row)

                if stale:
                    await db.commit()
                    logger.info("Hermes: recovered %d stale jobs", len(stale))
        except Exception as e:
            logger.warning("Hermes: job recovery failed: %s", e)

    async def load_recent_jobs(self, limit: int = 200) -> None:
        """Load recent job history from DB into memory cache at startup."""
        from app.database.session import async_session
        from app.database.models import HermesJob
        from sqlalchemy import select

        try:
            async with async_session() as db:
                result = await db.execute(
                    select(HermesJob)
                    .order_by(HermesJob.created_at.desc())
                    .limit(limit)
                )
                for row in result.scalars():
                    self._jobs[str(row.id)] = self._row_to_dict(row)
            logger.info("Hermes: loaded %d jobs from DB", len(self._jobs))
        except Exception as e:
            logger.warning("Hermes: could not load job history: %s", e)

    # ── Backward-compat: adopt already-running asyncio tasks ────────────

    def adopt_job(self, agent_name: str, task: str, running: asyncio.Task,
                  org_id: str | None = None) -> dict:
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = {
            "id": job_id, "agent": agent_name, "task": task,
            "status": JobStatus.RUNNING, "progress_percent": 10,
            "progress_message": "Running...", "result": None, "error": None,
            "org_id": org_id, "created_at": datetime.utcnow().isoformat(),
            "started_at": datetime.utcnow().isoformat(), "completed_at": None,
        }

        def _on_done(fut: asyncio.Task):
            job = self._jobs.get(job_id)
            if not job:
                return
            job["completed_at"] = datetime.utcnow().isoformat()
            try:
                result = fut.result()
                job["result"] = {
                    "message": result.message, "success": result.success,
                    "data": str(result.data) if result.data else None,
                }
                job["status"] = JobStatus.COMPLETED
                job["progress_percent"] = 100
            except Exception as e:
                job["status"] = JobStatus.FAILED
                job["error"] = str(e)

        running.add_done_callback(_on_done)
        return {"job_id": job_id, "status": JobStatus.RUNNING}

    async def _execute_tool(self, tool_name: str, args: dict, context: AgentContext = None) -> Any:
        return {"status": "hermes_is_not_llm_driven"}
