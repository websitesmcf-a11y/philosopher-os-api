from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.session import get_db
from app.core.security import get_current_user, get_current_org
from app.schemas.automation import AutomationRuleCreate, AutomationRuleUpdate
from app.services.automation_service import AutomationService

router = APIRouter()


@router.get("/rules")
async def list_rules(
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = AutomationService(db, org_id=org_id)
    return await service.list_rules()


@router.post("/rules", status_code=201)
async def create_rule(
    data: AutomationRuleCreate,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = AutomationService(db, org_id=org_id)
    return await service.create_rule(data)


@router.patch("/rules/{rule_id}")
async def update_rule(
    rule_id: str,
    data: AutomationRuleUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = AutomationService(db, org_id=org_id)
    return await service.update_rule(rule_id, data)


@router.delete("/rules/{rule_id}")
async def delete_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = AutomationService(db, org_id=org_id)
    return await service.delete_rule(rule_id)


@router.post("/rules/{rule_id}/test")
async def test_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = AutomationService(db, org_id=org_id)
    return await service.test_rule(rule_id, {})


@router.get("/jobs")
async def list_jobs(
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = AutomationService(db, org_id=org_id)
    return await service.list_jobs()
