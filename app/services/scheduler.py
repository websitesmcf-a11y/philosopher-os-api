"""In-process job scheduler — executes due ScheduledJob rows without Celery.

Started from the FastAPI lifespan. Polls the scheduled_jobs table and runs due
jobs. The flagship job type is ``campaign_drip_send``: send ONE personalized
message to the next pending lead of a drip campaign, then schedule the next
send at a random gap (default 40-60 minutes) so outreach looks human.

Job types:
- campaign_drip_send  {campaign_id}
- facebook_post       {message, link?}
- agent_task          {agent, task}    → runs a council agent autonomously
"""
import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.database.models import Campaign, CampaignLead, Lead, ScheduledJob
from app.database.session import async_session

logger = logging.getLogger(__name__)


def _personalize_fallback(template: str, lead: Lead) -> str:
    """Plain placeholder substitution when the LLM is unavailable."""
    return (
        template.replace("{name}", lead.name or "there")
        .replace("{company}", lead.company or "your business")
        .replace("{industry}", lead.industry or "your industry")
    )


async def personalize_message(template: str, lead: Lead) -> str:
    """Personalize a campaign template for one lead via the LLM, with a safe fallback."""
    base = _personalize_fallback(template, lead)
    try:
        from app.llm.client import llm
        response = await llm.generate(
            system=(
                "You personalize outreach messages. Rewrite the message naturally for the "
                "specific recipient. Keep the same intent, language, offer, and rough length. "
                "Output ONLY the final message — no preamble, no quotes, no placeholders."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Recipient: {lead.name or 'unknown'}"
                    f"{f' at {lead.company}' if lead.company and lead.company != lead.name else ''}"
                    f"{f' ({lead.industry})' if lead.industry else ''}\n"
                    f"Message template:\n{base}"
                ),
            }],
            temperature=0.7,
            max_tokens=400,
        )
        text = (response.content or "").strip()
        # Guard against an empty or runaway response — the template is the contract.
        if text and len(text) <= max(len(base) * 3, 600):
            return text
    except Exception as e:
        logger.debug(f"Personalization LLM unavailable, using template: {e}")
    return base


class JobScheduler:
    """Asyncio polling loop over the scheduled_jobs table."""

    def __init__(self, poll_seconds: int = 20):
        self.poll_seconds = poll_seconds
        self._task: asyncio.Task | None = None
        self._running = False
        self.jobs_executed = 0

    def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(f"Job scheduler started (poll every {self.poll_seconds}s)")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("Job scheduler stopped")

    async def _loop(self):
        while self._running:
            try:
                await self.tick()
            except Exception as e:
                logger.error(f"Scheduler tick failed: {e}")
            await asyncio.sleep(self.poll_seconds)

    async def tick(self) -> int:
        """Run every due pending job once. Returns the number executed."""
        executed = 0
        async with async_session() as db:
            now = datetime.now(timezone.utc)
            result = await db.execute(
                select(ScheduledJob).where(
                    ScheduledJob.status == "pending",
                    ScheduledJob.scheduled_for <= now,
                ).limit(10)
            )
            jobs = list(result.scalars().all())
            for job in jobs:
                job.status = "running"
                await db.flush()
                try:
                    outcome = await self._execute(db, job)
                    job.status = "completed"
                    job.result = outcome
                except Exception as e:
                    logger.error(f"Job {job.id} ({job.job_type}) failed: {e}")
                    job.retry_count = (job.retry_count or 0) + 1
                    if job.retry_count <= (job.max_retries or 3):
                        job.status = "pending"
                        job.scheduled_for = now + timedelta(minutes=5 * job.retry_count)
                        job.error = f"retry {job.retry_count}: {e}"
                    else:
                        job.status = "failed"
                        job.error = str(e)
                await db.commit()
                executed += 1
                self.jobs_executed += 1

        # Also check for due tasks with upcoming due_date. Use a fresh session:
        # the `async with` above has already closed `db` by this point, and we
        # need to commit the task status changes that execute_task() makes.
        try:
            from app.services.task_executor import check_due_tasks
            async with async_session() as task_db:
                task_count = await check_due_tasks(task_db)
                await task_db.commit()
            if task_count:
                logger.info(f"Executed {task_count} due task(s)")
                executed += task_count
        except Exception as e:
            logger.error(f"Task scheduler check failed: {e}")

        return executed

    async def _execute(self, db, job: ScheduledJob) -> dict:
        payload = job.payload or {}
        if job.job_type == "campaign_drip_send":
            return await self._drip_send(db, job, payload)
        if job.job_type == "facebook_post":
            from app.integrations.facebook import post_to_page
            return await post_to_page(db, payload.get("message", ""), link=payload.get("link"))
        if job.job_type == "agent_task":
            return await self._agent_task(db, job, payload)
        return {"status": "unknown_job_type", "job_type": job.job_type}

    async def _drip_send(self, db, job: ScheduledJob, payload: dict) -> dict:
        campaign_id = payload.get("campaign_id")
        result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
        campaign = result.scalar_one_or_none()
        if not campaign:
            return {"status": "campaign_not_found", "campaign_id": campaign_id}
        if campaign.status != "active":
            return {"status": "campaign_inactive", "campaign_status": campaign.status}

        # Next pending lead for this campaign.
        result = await db.execute(
            select(CampaignLead, Lead)
            .join(Lead, Lead.id == CampaignLead.lead_id)
            .where(
                CampaignLead.campaign_id == campaign.id,
                CampaignLead.status == "pending",
            )
            .limit(1)
        )
        row = result.first()
        if not row:
            campaign.status = "completed"
            await db.flush()
            return {"status": "campaign_completed", "sent_total": campaign.sent_count}

        campaign_lead, lead = row
        config = campaign.schedule_config or {}
        message = campaign.message_template
        if config.get("personalize", True):
            message = await personalize_message(campaign.message_template, lead)

        from app.services.delivery import deliver_to_lead
        delivery = await deliver_to_lead(db, campaign.org_id, lead, campaign.channel, message)

        if delivery.get("status") == "sent":
            campaign_lead.status = "sent"
            campaign_lead.sent_at = datetime.now(timezone.utc)
            campaign.sent_count = (campaign.sent_count or 0) + 1
        elif delivery.get("status") == "skipped":
            campaign_lead.status = "skipped"
        else:
            campaign_lead.status = "failed"
        await db.flush()

        # More pending leads? Schedule the next send at a random human-like gap.
        remaining = await db.execute(
            select(CampaignLead).where(
                CampaignLead.campaign_id == campaign.id,
                CampaignLead.status == "pending",
            ).limit(1)
        )
        next_info = None
        if remaining.first():
            gap_min = int(config.get("interval_min_minutes", 40))
            gap_max = max(gap_min, int(config.get("interval_max_minutes", 60)))
            delay = random.randint(gap_min * 60, gap_max * 60)
            next_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            db.add(ScheduledJob(
                org_id=job.org_id,
                job_type="campaign_drip_send",
                payload={"campaign_id": str(campaign.id)},
                scheduled_for=next_at,
                status="pending",
            ))
            await db.flush()
            next_info = next_at.isoformat()
        else:
            campaign.status = "completed"
            await db.flush()

        return {
            "status": "executed",
            "lead": lead.name,
            "delivery": delivery.get("status"),
            "detail": delivery.get("reason") or delivery.get("error"),
            "next_send_at": next_info,
        }

    async def _agent_task(self, db, job: ScheduledJob, payload: dict) -> dict:
        from app.agents.base import AgentContext
        council = _get_council()
        if not council:
            return {"status": "council_unavailable"}
        agent = council.agents.get(payload.get("agent", ""))
        if not agent:
            return {"status": "agent_not_found", "agent": payload.get("agent")}
        context = AgentContext(
            user_input=payload.get("task", ""),
            org_id=str(job.org_id) if job.org_id else None,
            db_session=db,
        )
        result = await agent.run(context)
        return {"status": "executed", "success": result.success, "message": (result.message or "")[:1000]}


def _get_council():
    try:
        from app.main import council
        return council
    except Exception:
        return None


scheduler = JobScheduler()
