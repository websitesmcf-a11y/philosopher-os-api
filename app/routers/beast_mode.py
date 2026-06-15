"""Beast Mode API Router — Multi-Agent Mission Orchestration"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/beast-mode", tags=["Beast Mode"])


class PlanMissionRequest(BaseModel):
    objective: str
    agents: list[str] = []
    mode: str = "dry_run"


class ExecuteMissionRequest(BaseModel):
    objective: str
    agents: list[str]
    mode: str = "approved"


@router.get("/status")
async def get_beast_mode_status():
    """Check Beast Mode availability and RUflow status."""
    from app.services.beast_mode import beast_mode as bm
    ruflow_ok = await bm.check_ruflow()
    return {
        "available": True,
        "ruflow_connected": ruflow_ok,
        "max_agents": 15,
        "levels_available": ["dry_run", "assisted", "approved", "full"],
    }


@router.post("/plan")
async def plan_mission(req: PlanMissionRequest):
    """Plan a Beast Mode mission with safety analysis."""
    from app.services.beast_mode import beast_mode as bm, BeastLevel

    level_map = {l.value: l for l in BeastLevel}
    level = level_map.get(req.mode, BeastLevel.DRY_RUN)

    class SimpleCtx:
        workspace_id = None
        db_session = None

    plan = await bm.plan_mission(SimpleCtx(), req.objective, req.agents, level)
    return plan


@router.post("/execute")
async def execute_mission(req: ExecuteMissionRequest):
    """Execute an approved Beast Mode mission."""
    from app.services.beast_mode import beast_mode as bm, BeastLevel

    level_map = {l.value: l for l in BeastLevel}
    level = level_map.get(req.mode, BeastLevel.APPROVED)

    if level == BeastLevel.DRY_RUN:
        raise HTTPException(status_code=400, detail="Cannot execute a dry-run mission. Change mode to 'assisted', 'approved', or 'full'.")

    class MissionCtx:
        workspace_id = None
        org_id = "00000000-0000-0000-0000-000000000001"  # Default dev org

    plan = await bm.plan_mission(MissionCtx(), req.objective, req.agents, level)
    execution = await bm.execute_mission(MissionCtx(), plan)
    return execution


@router.post("/{mission_id}/{action}")
async def control_mission(mission_id: str, action: str):
    """Pause, resume, or cancel a mission."""
    if action not in ["pause", "resume", "cancel"]:
        raise HTTPException(status_code=400, detail="Action must be pause, resume, or cancel")
    from app.services.beast_mode import beast_mode as bm
    return await bm.control_mission(mission_id, action)


@router.get("/{mission_id}")
async def get_mission(mission_id: str):
    """Get mission status and logs."""
    from app.services.beast_mode import beast_mode as bm
    status = await bm.get_mission_status(mission_id)
    if not status:
        raise HTTPException(status_code=404, detail="Mission not found")
    return status
