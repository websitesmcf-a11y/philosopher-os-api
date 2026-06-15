import uuid
import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from app.database.models import AutomationRule, ScheduledJob
from app.schemas.automation import AutomationRuleCreate, AutomationRuleUpdate

logger = logging.getLogger(__name__)


class AutomationService:
    def __init__(self, db: AsyncSession, org_id: str):
        self.db = db
        self.org_id = org_id

    async def list_rules(self, page: int = 1, page_size: int = 20, **filters):
        query = select(AutomationRule).where(AutomationRule.org_id == self.org_id)
        if "trigger_event" in filters:
            query = query.where(AutomationRule.trigger_event == filters["trigger_event"])
        if "enabled" in filters:
            query = query.where(AutomationRule.enabled == filters["enabled"])

        count_q = select(func.count()).select_from(query.subquery())
        total = (await self.db.execute(count_q)).scalar() or 0

        query = query.order_by(AutomationRule.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        items = result.scalars().all()

        return {
            "items": [self._rule_to_response(r) for r in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def create_rule(self, data: AutomationRuleCreate):
        rule = AutomationRule(
            id=uuid.uuid4(),
            org_id=uuid.UUID(self.org_id) if isinstance(self.org_id, str) else self.org_id,
            **data.model_dump(exclude_none=True),
        )
        self.db.add(rule)
        await self.db.flush()
        return self._rule_to_response(rule)

    async def update_rule(self, rule_id: str, data: AutomationRuleUpdate):
        result = await self.db.execute(
            select(AutomationRule).where(AutomationRule.id == rule_id, AutomationRule.org_id == self.org_id)
        )
        rule = result.scalar_one_or_none()
        if not rule:
            from app.core.errors import NotFoundError
            raise NotFoundError("Automation rule not found")
        for key, val in data.model_dump(exclude_none=True).items():
            setattr(rule, key, val)
        await self.db.flush()
        return self._rule_to_response(rule)

    async def delete_rule(self, rule_id: str):
        result = await self.db.execute(
            select(AutomationRule).where(AutomationRule.id == rule_id, AutomationRule.org_id == self.org_id)
        )
        rule = result.scalar_one_or_none()
        if not rule:
            from app.core.errors import NotFoundError
            raise NotFoundError("Automation rule not found")
        await self.db.delete(rule)

    async def test_rule(self, rule_id: str, payload: dict) -> dict:
        result = await self.db.execute(
            select(AutomationRule).where(AutomationRule.id == rule_id, AutomationRule.org_id == self.org_id)
        )
        rule = result.scalar_one_or_none()
        if not rule:
            from app.core.errors import NotFoundError
            raise NotFoundError("Automation rule not found")
        return {
            "triggered": True,
            "actions_to_execute": rule.actions,
            "matched_conditions": True,
        }

    async def list_jobs(self, page: int = 1, page_size: int = 20, **filters):
        query = select(ScheduledJob).where(ScheduledJob.org_id == self.org_id)
        if "status" in filters:
            query = query.where(ScheduledJob.status == filters["status"])
        if "job_type" in filters:
            query = query.where(ScheduledJob.job_type == filters["job_type"])

        count_q = select(func.count()).select_from(query.subquery())
        total = (await self.db.execute(count_q)).scalar() or 0

        query = query.order_by(ScheduledJob.scheduled_for.desc()).offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        items = result.scalars().all()

        return {
            "items": [self._job_to_response(j) for j in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def _rule_to_response(self, rule: AutomationRule):
        return {
            "id": str(rule.id),
            "org_id": str(rule.org_id),
            "name": rule.name,
            "trigger_event": rule.trigger_event,
            "conditions": rule.conditions or {},
            "actions": rule.actions,
            "enabled": rule.enabled,
            "last_run_at": rule.last_run_at,
            "created_at": rule.created_at,
            "updated_at": rule.updated_at,
        }

    def _job_to_response(self, job: ScheduledJob):
        return {
            "id": str(job.id),
            "org_id": str(job.org_id),
            "job_type": job.job_type,
            "payload": job.payload,
            "scheduled_for": job.scheduled_for,
            "status": job.status,
            "result": job.result,
            "error": job.error,
            "retry_count": job.retry_count,
            "max_retries": job.max_retries,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }
