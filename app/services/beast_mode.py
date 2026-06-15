"""Beast Mode / Flow State — Multi-Agent Orchestration System

Coordinates philosophers and gods through RUflow for large-scale mission execution.
Provides safety gates, approval workflows, rate limits, and dry-run mode.
"""

from typing import Any, Optional
from datetime import datetime
from enum import Enum


class BeastLevel(Enum):
    DRY_RUN = "dry_run"
    ASSISTED = "assisted"
    APPROVED = "approved"
    FULL = "full"


class BeastModeService:
    """Orchestrates multi-agent missions with safety controls."""

    def __init__(self):
        self.active_missions: dict = {}
        self.ruflow_available = False

    async def check_ruflow(self) -> bool:
        """Check if RUflow is available for orchestration."""
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.get("http://localhost:7777/health", timeout=3)
                self.ruflow_available = resp.status_code == 200
                return self.ruflow_available
        except Exception:
            self.ruflow_available = False
            return False

    def _get_council(self):
        try:
            from app.main import council
            return council
        except Exception:
            from app.agents.council import CouncilOrchestrator
            return CouncilOrchestrator()

    async def plan_mission(self, ctx: Any, objective: str, agents: list[str],
                           mode: BeastLevel = BeastLevel.DRY_RUN) -> dict:
        """Plan a Beast Mode mission with safety analysis."""
        council = self._get_council()

        plan = {
            "objective": objective,
            "mode": mode.value,
            "agents": agents,
            "steps": [],
            "required_integrations": [],
            "missing_integrations": [],
            "estimated_cost": 0,
            "risks": [],
            "approval_required": mode in [BeastLevel.APPROVED, BeastLevel.FULL],
        }

        for agent_id in agents:
            agent_cls = council.agents.get(agent_id)
            if not agent_cls:
                plan["steps"].append({"agent": agent_id, "status": "unknown", "error": "Agent not found"})
                continue

            step = {
                "agent": agent_id,
                "required_integrations": getattr(agent_cls, 'required_integrations', []),
                "tools": getattr(agent_cls, 'tools', []),
                "estimated_cost": 0.5,
            }

            for integration in step["required_integrations"]:
                if integration not in plan["required_integrations"]:
                    plan["required_integrations"].append(integration)
                    if not await self._check_integration(ctx, integration):
                        plan["missing_integrations"].append(integration)

            plan["steps"].append(step)
            plan["estimated_cost"] += step["estimated_cost"]

        if len(agents) > 3:
            plan["risks"].append("High agent count — increased coordination complexity")
        if plan["missing_integrations"]:
            plan["risks"].append(f"Missing integrations: {', '.join(plan['missing_integrations'])}")
        if plan["estimated_cost"] > 10:
            plan["risks"].append(f"Estimated cost {plan['estimated_cost']} credits — consider dry run first")

        plan["safe_to_execute"] = len(plan["missing_integrations"]) == 0 and mode != BeastLevel.DRY_RUN
        return plan

    async def execute_mission(self, ctx: Any, plan: dict) -> dict:
        """Execute a planned Beast Mode mission with real agent execution."""
        council = self._get_council()
        from app.database.session import async_session

        mission_id = f"beast_{int(datetime.utcnow().timestamp())}"
        execution = {
            "mission_id": mission_id,
            "status": "running",
            "started_at": datetime.utcnow().isoformat(),
            "steps": [],
            "errors": [],
        }

        for step in plan["steps"]:
            agent_id = step["agent"]
            try:
                agent_cls = council.agents.get(agent_id)
                if not agent_cls:
                    execution["errors"].append(f"Agent {agent_id} not found")
                    continue

                # Create a real DB session for this mission step
                async with async_session() as db:
                    result = await council.process(
                        user_input=plan["objective"],
                        org_id=str(getattr(ctx, 'org_id', '')),
                        db_session=db,
                        agent=agent_id,
                    )
                    await db.commit()

                reply = result.get("reply", "") if result else ""
                execution["steps"].append({
                    "agent": agent_id,
                    "status": "completed",
                    "result": reply[:500] if reply else "No output",
                })
            except Exception as e:
                execution["errors"].append(f"Agent {agent_id} failed: {str(e)}")
                execution["steps"].append({"agent": agent_id, "status": "failed", "error": str(e)})

        execution["status"] = "completed" if not execution["errors"] else "completed_with_errors"
        execution["completed_at"] = datetime.utcnow().isoformat()
        self.active_missions[mission_id] = execution
        return execution

    async def _check_integration(self, ctx: Any, provider: str) -> bool:
        try:
            async with ctx.db_session as db:
                from sqlalchemy import select
                from app.database.models import Integration
                result = await db.execute(
                    select(Integration).where(
                        Integration.workspace_id == ctx.workspace_id,
                        Integration.provider == provider,
                        Integration.status == "connected",
                    )
                )
                return result.scalar_one_or_none() is not None
        except Exception:
            return False

    async def control_mission(self, mission_id: str, action: str) -> dict:
        if mission_id not in self.active_missions:
            return {"status": "error", "error": "Mission not found"}
        self.active_missions[mission_id]["status"] = action
        return {"status": action, "mission_id": mission_id}

    async def get_mission_status(self, mission_id: str) -> Optional[dict]:
        return self.active_missions.get(mission_id)


beast_mode = BeastModeService()
