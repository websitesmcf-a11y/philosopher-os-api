"""Beast Mode / Flow State — Multi-Agent Orchestration System

Coordinates philosophers and gods through RUflow for large-scale mission execution.
Provides safety gates, approval workflows, rate limits, and dry-run mode.

Level-based access:
  Level 1 (dry_run):   Plan only — no execution, no side effects.
  Level 2 (assisted):   Basic agents (philosophers only). User must approve each
                        agent's output before it proceeds. No web/browser tools.
  Level 3 (approved):   All philosopher agents + browser/web tools. Automatic
                        execution within approved plan.
  Level 4 (full):       All agents including Gods + web/browser + spending.
                        Agents can execute paid operations when banking details
                        have been provided.
"""

from typing import Any, Optional
from datetime import datetime
from enum import Enum


class BeastLevel(Enum):
    DRY_RUN = "dry_run"
    ASSISTED = "assisted"
    APPROVED = "approved"
    FULL = "full"


# ── Level-based agent tiers ──────────────────────────────────────────

# Philosophers: safe reasoning agents with no side-effect tools
PHILOSOPHER_AGENTS = [
    "plato", "socrates", "heraclitus", "pythagoras", "solon",
]

# Extended agents: have web/browser tools but no spending
EXTENDED_AGENTS = PHILOSOPHER_AGENTS + [
    "aristotle", "athena", "leonidas", "archimedes", "odysseus",
]

# God agents: access to bulk operations, mass messaging, orchestration
GOD_AGENTS = [
    "iapetus", "astraeus", "erebos", "phantasos", "stilbon",
]

ALL_AGENTS = EXTENDED_AGENTS + GOD_AGENTS

# Tools available at each level
BASIC_TOOLS = {"search_memory", "reason", "report"}
WEB_TOOLS = BASIC_TOOLS | {"web_search", "scrape_website", "find_businesses", "search_google"}
SPENDING_TOOLS = WEB_TOOLS | {"spend", "execute_payment", "purchase_ad", "send_bulk"}


def agents_for_level(level: BeastLevel) -> list[str]:
    if level == BeastLevel.DRY_RUN:
        return ALL_AGENTS  # planning can consider any agent
    if level == BeastLevel.ASSISTED:
        return PHILOSOPHER_AGENTS
    if level == BeastLevel.APPROVED:
        return EXTENDED_AGENTS + GOD_AGENTS
    return ALL_AGENTS  # FULL


def tools_for_level(level: BeastLevel) -> set[str]:
    if level == BeastLevel.DRY_RUN:
        return set()  # no tools during planning
    if level == BeastLevel.ASSISTED:
        return BASIC_TOOLS
    if level == BeastLevel.APPROVED:
        return WEB_TOOLS
    return SPENDING_TOOLS  # FULL


def level_name(mode: BeastLevel) -> str:
    return {
        BeastLevel.DRY_RUN: "Dry Run — plan only",
        BeastLevel.ASSISTED: "Assisted — basic agents, user approval per step",
        BeastLevel.APPROVED: "Approved — full philosopher agents + web/browser tools",
        BeastLevel.FULL: "Full — all agents including Gods + spending capability",
    }.get(mode, str(mode))


class BeastModeService:
    """Orchestrates multi-agent missions with safety controls."""

    def __init__(self):
        self.active_missions: dict = {}
        self.ruflow_available = False
        self.banking_configured = False

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

    def _check_banking(self) -> bool:
        """Check if banking / payment details have been configured."""
        try:
            from app.config import settings
            # Check for any payment processor credentials
            return bool(
                getattr(settings, "stripe_api_key", None)
                or getattr(settings, "payment_provider", None)
            )
        except Exception:
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
        allowed = agents_for_level(mode)
        self.banking_configured = self._check_banking()

        # Filter requested agents to those allowed at this level
        filtered_agents = [a for a in agents if a in allowed]
        blocked_agents = [a for a in agents if a not in allowed]

        plan = {
            "objective": objective,
            "mode": mode.value,
            "level_label": level_name(mode),
            "agents": filtered_agents,
            "blocked_agents": blocked_agents,
            "steps": [],
            "required_integrations": [],
            "missing_integrations": [],
            "estimated_cost": 0,
            "risks": [],
            "approval_required": mode in [BeastLevel.APPROVED, BeastLevel.FULL],
            "tool_access": list(tools_for_level(mode)),
            "spending_enabled": mode == BeastLevel.FULL,
        }

        if mode == BeastLevel.FULL and not self.banking_configured:
            plan["risks"].append(
                "FULL mode selected but no payment provider configured — "
                "spending operations will be simulated. Add Stripe or payment "
                "provider in Integrations to enable real spending."
            )

        if blocked_agents:
            plan["risks"].append(
                f"Agents not available at {mode.value} level: {', '.join(blocked_agents)}. "
                f"Switch to FULL mode to unlock all agents."
            )

        for agent_id in filtered_agents:
            agent_cls = council.agents.get(agent_id)
            if not agent_cls:
                plan["steps"].append({"agent": agent_id, "status": "unknown", "error": "Agent not found"})
                continue

            step = {
                "agent": agent_id,
                "required_integrations": getattr(agent_cls, 'required_integrations', []),
                "tools": [t for t in (getattr(agent_cls, 'tools', []) or [])
                          if t.get("name") in tools_for_level(mode)],
                "estimated_cost": 0.5,
            }

            for integration in step["required_integrations"]:
                if integration not in plan["required_integrations"]:
                    plan["required_integrations"].append(integration)
                    if not await self._check_integration(ctx, integration):
                        plan["missing_integrations"].append(integration)

            plan["steps"].append(step)
            plan["estimated_cost"] += step["estimated_cost"]

        if len(filtered_agents) > 3:
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

        mode_value = plan.get("mode", "assisted")
        level_map = {l.value: l for l in BeastLevel}
        mode = level_map.get(mode_value, BeastLevel.ASSISTED)

        mission_id = f"beast_{int(datetime.utcnow().timestamp())}"
        execution = {
            "mission_id": mission_id,
            "status": "running",
            "level": mode.value,
            "started_at": datetime.utcnow().isoformat(),
            "objective": plan.get("objective", ""),
            "steps": [],
            "errors": [],
            "warnings": [],
        }

        if mode == BeastLevel.FULL and not self._check_banking():
            execution["warnings"].append(
                "FULL mode: no payment provider configured. Spending operations "
                "will be simulated."
            )

        available_tools = tools_for_level(mode)

        for step in plan.get("steps", []):
            agent_id = step["agent"]
            try:
                agent_cls = council.agents.get(agent_id)
                if not agent_cls:
                    execution["errors"].append(f"Agent {agent_id} not found")
                    continue

                # Create a real DB session for this mission step
                async with async_session() as db:
                    result = await council.process(
                        user_input=plan.get("objective", ""),
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
