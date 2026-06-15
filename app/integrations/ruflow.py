"""Ruflo adapter — agent memory, routing, and workflow orchestration via the ruflo CLI.

Ruflo (AI Agent Orchestration Platform) is installed globally via npm. This
adapter shells out to the CLI so every council agent can persist memories,
search past work, and route tasks through ruflo's Q-learning router.
"""
import asyncio
import json
import logging
import shutil
import sys
from typing import Any

logger = logging.getLogger(__name__)


class RufloClient:
    """Thin subprocess wrapper around the ``ruflo`` CLI."""

    def __init__(self):
        self._path: str | None = None
        self._checked = False

    @property
    def available(self) -> bool:
        if not self._checked:
            # npm installs ruflo.cmd / ruflo.ps1 on Windows; shutil.which finds .cmd via PATHEXT
            self._path = shutil.which("ruflo") or shutil.which("ruflo.cmd")
            self._checked = True
        return self._path is not None

    async def _run(self, *args: str, timeout: float = 60.0) -> dict:
        if not self.available:
            return {
                "status": "not_installed",
                "message": "ruflo CLI not found on PATH (npm install -g ruflo).",
            }
        try:
            if sys.platform == "win32":
                # .cmd shims need the shell to resolve
                cmd = " ".join([f'"{self._path}"'] + [f'"{a}"' for a in args])
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    self._path, *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            out = stdout.decode("utf-8", errors="replace").strip()
            err = stderr.decode("utf-8", errors="replace").strip()
            if proc.returncode == 0:
                return {"status": "success", "output": out[:3000]}
            return {"status": "error", "output": out[:1500], "error": err[:1500]}
        except asyncio.TimeoutError:
            return {"status": "timeout", "message": f"ruflo command exceeded {timeout}s"}
        except Exception as e:
            logger.warning(f"ruflo run failed: {e}")
            return {"status": "error", "message": str(e)}

    async def memory_store(self, key: str, value: str, namespace: str = "socrates") -> dict:
        """Persist a memory entry in ruflo's cross-session memory."""
        return await self._run("memory", "store", key, value, "--namespace", namespace)

    async def memory_search(self, query: str, namespace: str = "socrates") -> dict:
        """Search ruflo's cross-session memory."""
        return await self._run("memory", "query", query, "--namespace", namespace)

    async def route_task(self, task: str) -> dict:
        """Ask ruflo's Q-learning router which agent should own a task."""
        return await self._run("route", task)

    async def execute_workflow(self, workflow_name: str, payload: dict) -> dict:
        """Execute a ruflo workflow by name."""
        return await self._run("workflow", "run", workflow_name, "--input", json.dumps(payload))

    async def create_workflow(self, name: str, steps: list[dict]) -> dict:
        return await self._run("workflow", "create", name, "--steps", json.dumps(steps))

    async def schedule_workflow(self, workflow_name: str, cron: str, payload: dict) -> dict:
        return await self._run(
            "workflow", "schedule", workflow_name, "--cron", cron, "--input", json.dumps(payload)
        )


ruflow = RufloClient()
ruflo = ruflow  # preferred alias
