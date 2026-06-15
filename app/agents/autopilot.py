"""Autopilot — periodic autonomous agent operations loop."""

import asyncio
import logging
from datetime import datetime, timezone
from app.agents.base import AgentContext

logger = logging.getLogger(__name__)


class Autopilot:
    """Periodic loop that delegates routine tasks to council agents."""

    def __init__(self, council=None):
        self.council = council
        self._task: asyncio.Task | None = None
        self._running = False
        self.last_run: datetime | None = None
        self.actions_taken = 0
        self.interval_seconds = 300

    async def _loop(self):
        logger.info("Autopilot loop started")
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.error(f"Autopilot tick error: {e}")
            self.last_run = datetime.now(timezone.utc)
            await asyncio.sleep(self.interval_seconds)

    async def _tick(self) -> int:
        """Execute one autopilot cycle. Returns number of actions taken."""
        if not self.council:
            logger.warning("Autopilot tick skipped — no council registered")
            return 0

        actions = 0

        # Delegate to Odysseus for unassigned lead outreach
        odysseus = self.council.agents.get("odysseus")
        if odysseus:
            try:
                ctx = AgentContext(
                    user_input="Check for new unassigned leads that need initial outreach. Find leads with status 'new' and suggest first contact.",
                    org_id=None,
                    db_session=None,
                )
                result = await odysseus.run(ctx)
                if result.success:
                    logger.info(f"Autopilot Odysseus: {result.message[:100]}")
                actions += 1
            except Exception as e:
                logger.error(f"Autopilot Odysseus error: {e}")

        # Delegate to Pythagoras for campaign performance check
        pythagoras = self.council.agents.get("pythagoras")
        if pythagoras:
            try:
                ctx = AgentContext(
                    user_input="Review current campaign performance metrics. Check sent counts, reply rates, and conversion rates across all active campaigns.",
                    org_id=None,
                    db_session=None,
                )
                result = await pythagoras.run(ctx)
                if result.success:
                    logger.info(f"Autopilot Pythagoras: {result.message[:100]}")
                actions += 1
            except Exception as e:
                logger.error(f"Autopilot Pythagoras error: {e}")

        # Delegate to Leonidas for operations health check
        leonidas = self.council.agents.get("leonidas")
        if leonidas:
            try:
                ctx = AgentContext(
                    user_input="Run a system operations check. Verify API health, database connectivity, and worker queue status.",
                    org_id=None,
                    db_session=None,
                )
                result = await leonidas.run(ctx)
                if result.success:
                    logger.info(f"Autopilot Leonidas: {result.message[:100]}")
                actions += 1
            except Exception as e:
                logger.error(f"Autopilot Leonidas error: {e}")

        # Delegate to Plato for executive summary
        plato = self.council.agents.get("plato")
        if plato:
            try:
                ctx = AgentContext(
                    user_input="Provide a brief executive summary of current state. Synthesize operations, outreach, and campaign status into 3 bullet points.",
                    org_id=None,
                    db_session=None,
                )
                result = await plato.run(ctx)
                if result.success:
                    logger.info(f"Autopilot Plato: {result.message[:100]}")
                actions += 1
            except Exception as e:
                logger.error(f"Autopilot Plato error: {e}")

        self.actions_taken += actions
        if actions:
            logger.info(f"Autopilot tick: {actions} actions delegated")
        return actions

    def start(self, interval_seconds: int = 300):
        if self._running:
            logger.warning("Autopilot already running")
            return
        self._running = True
        self.interval_seconds = interval_seconds
        self._task = asyncio.create_task(self._loop())
        logger.info(f"Autopilot started (interval={interval_seconds}s)")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("Autopilot stopped")

    @property
    def status(self) -> dict:
        return {
            "running": self._running,
            "interval_seconds": self.interval_seconds,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "actions_taken": self.actions_taken,
        }
