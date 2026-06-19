"""Beast Mode API Router — Multi-Agent Mission Orchestration"""

import asyncio
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


# Background mission execution — prevents Railway proxy timeout on long missions.
_BACKGROUND_MISSIONS: dict[str, asyncio.Task] = {}


@router.get("/status")
async def get_beast_mode_status():
    """Check Beast Mode availability and level info."""
    from app.services.beast_mode import beast_mode as bm, BeastLevel, level_name, agents_for_level, tools_for_level

    ruflow_ok = await bm.check_ruflow()
    banking_ok = bm._check_banking()

    levels = []
    for lv in BeastLevel:
        levels.append({
            "id": lv.value,
            "label": level_name(lv),
            "agents_available": agents_for_level(lv),
            "tools_available": list(tools_for_level(lv)),
        })

    return {
        "available": True,
        "ruflow_connected": ruflow_ok,
        "banking_configured": banking_ok,
        "max_agents": 15,
        "levels": levels,
        "all_agents": [
            {"id": "plato", "name": "Plato", "tier": "philosopher"},
            {"id": "socrates", "name": "Socrates", "tier": "philosopher"},
            {"id": "heraclitus", "name": "Heraclitus", "tier": "philosopher"},
            {"id": "pythagoras", "name": "Pythagoras", "tier": "philosopher"},
            {"id": "solon", "name": "Solon", "tier": "philosopher"},
            {"id": "aristotle", "name": "Aristotle", "tier": "extended"},
            {"id": "athena", "name": "Athena", "tier": "extended"},
            {"id": "leonidas", "name": "Leonidas", "tier": "extended"},
            {"id": "archimedes", "name": "Archimedes", "tier": "extended"},
            {"id": "odysseus", "name": "Odysseus", "tier": "extended"},
            {"id": "iapetus", "name": "Iapetus", "tier": "god"},
            {"id": "astraeus", "name": "Astraeus", "tier": "god"},
            {"id": "erebos", "name": "Erebos", "tier": "god"},
            {"id": "phantasos", "name": "Phantasos", "tier": "god"},
            {"id": "stilbon", "name": "Stilbon", "tier": "god"},
        ],
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
    """Start a Beast Mode mission in the background and return immediately.

    The frontend polls ``GET /api/v1/beast-mode/{mission_id}`` for incremental
    progress (status, errors, completed steps). The backend runs agents in a
    background ``asyncio.Task`` so Railway's HTTP proxy timeout (~60s) doesn't
    kill long-running missions.

    Level 4 (FULL) runs ALL selected agents in PARALLEL — expect near-instant
    completion for reasoning agents alongside longer lead-gen agents.
    """
    from app.services.beast_mode import beast_mode as bm, BeastLevel

    level_map = {l.value: l for l in BeastLevel}
    level = level_map.get(req.mode, BeastLevel.APPROVED)

    if level == BeastLevel.DRY_RUN:
        raise HTTPException(status_code=400, detail="Cannot execute a dry-run mission. Change mode to 'assisted', 'approved', or 'full'.")

    class MissionCtx:
        workspace_id = None
        org_id = "00000000-0000-0000-0000-000000000001"  # Default dev org

    plan = await bm.plan_mission(MissionCtx(), req.objective, req.agents, level)
    result = await bm.start_mission_async(MissionCtx(), plan)
    return result


@router.post("/test-parallel")
async def test_parallel_execution():
    """Run a quick parallel execution test with 3 dummy agents.

    Level 4 FULL mode: all 3 agents run simultaneously.
    Returns how long each took, proving parallel execution.
    """
    from app.services.beast_mode import beast_mode as bm, BeastLevel
    import time

    class TestCtx:
        workspace_id = None
        org_id = "00000000-0000-0000-0000-000000000001"

    # Create a test plan with 3 agents
    plan = {
        "objective": "Run a quick parallel test. Each agent should complete in 2-4 seconds.",
        "mode": "full",
        "agents": ["heraclitus", "pythagoras", "solon"],
        "steps": [
            {"agent": "heraclitus"},
            {"agent": "pythagoras"},
            {"agent": "solon"},
        ],
    }

    start = time.monotonic()
    result = await bm.execute_mission(TestCtx(), plan)
    elapsed = time.monotonic() - start

    steps = result.get("steps", [])
    return {
        "test": "parallel_execution",
        "mode": "full",
        "total_wall_time_seconds": round(elapsed, 2),
        "agents_executed": len(steps),
        "parallel_efficiency": f"If sequential, 3 agents at ~3s each = ~9s total. Actual: {round(elapsed, 2)}s — {'✅ PARALLEL' if elapsed < 8 else '⚠️ SEQUENTIAL (check for issues)'}",
        "steps": [
            {
                "agent": s.get("agent"),
                "status": s.get("status"),
                "result_preview": (s.get("result") or "")[:100],
            }
            for s in steps
        ],
        "errors": result.get("errors", []),
    }


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
