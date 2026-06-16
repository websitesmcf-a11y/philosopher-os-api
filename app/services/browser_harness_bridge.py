"""Browser Harness Bridge — WebSocket relay to a locally-running browser harness.

The user installs a small CLI agent on their computer that connects to Philosopher
OS via WebSocket. When agents need browser actions (Google Maps scraping, directory
lookups, logged-in-site access), the backend sends commands through this bridge
and the local harness runs them against the user's Chrome via CDP.

Protocol (JSON over WebSocket):
  Backend → Client: {"type": "run_script", "id": "<uuid>", "script": "..."}
  Client → Backend: {"type": "result", "id": "<uuid>", "status": "success|error", "output": "...", "error": "..."}
  Client → Backend: {"type": "ping"}
  Backend → Client: {"type": "pong"}
  Client → Backend: {"type": "status", "available": bool, "browser": str | None, "cdp": bool}
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

_COMMAND_TIMEOUT = 150.0  # max seconds to wait for a command result


class BrowserHarnessBridge:
    """Manages one WebSocket connection from a local browser-harness agent.

    Only one harness client can be connected at a time — if another connects,
    the previous one is disconnected.
    """

    def __init__(self):
        self._ws: WebSocket | None = None
        self._lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future] = {}
        self._connected = False
        self._client_available = False  # whether the client's browser-harness CLI works
        self._client_info: dict = {}  # extra status from the client

    # ── Connection lifecycle ──────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def client_available(self) -> bool:
        """True when a harness is connected AND its local browser-harness CLI is functional."""
        return self._connected and self._client_available

    @property
    def status(self) -> dict:
        return {
            "connected": self._connected,
            "available": self.client_available,
            "client_info": self._client_info,
        }

    async def connect(self, ws: WebSocket) -> None:
        """Accept a new harness connection, replacing any previous one."""
        async with self._lock:
            # Disconnect the old client if any
            if self._ws is not None:
                try:
                    await self._ws.close(code=1000, reason="Replaced by new client")
                except Exception:
                    pass
            self._ws = ws
            self._connected = True
            self._client_available = False
            self._client_info = {}
            logger.info("Browser harness client connected")

    async def disconnect(self) -> None:
        """Clean up on client disconnect or error."""
        async with self._lock:
            self._connected = False
            self._client_available = False
            self._client_info = {}
            # Fail all pending commands
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(RuntimeError("Harness disconnected"))
            self._pending.clear()
            self._ws = None
            logger.info("Browser harness client disconnected")

    async def handle_message(self, raw: str) -> None:
        """Process an incoming WebSocket message from the harness client."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("type")

        if msg_type == "ping":
            await self._send_json({"type": "pong"})

        elif msg_type == "status":
            self._client_available = bool(msg.get("available", False))
            self._client_info = {
                "browser": msg.get("browser"),
                "cdp": bool(msg.get("cdp", False)),
                "harness_version": msg.get("version", ""),
            }

        elif msg_type == "result":
            cmd_id = msg.get("id")
            if cmd_id and cmd_id in self._pending:
                fut = self._pending.pop(cmd_id)
                if not fut.done():
                    fut.set_result(msg)

        # Unknown message types are silently ignored

    # ── Command execution ─────────────────────────────────────────────

    async def run_script(self, script: str, *, timeout: float = _COMMAND_TIMEOUT) -> dict:
        """Send a script to the connected harness and wait for the result.

        Returns the result dict from the client. Raises ``ConnectionError`` if
        no harness is connected, ``asyncio.TimeoutError`` if the script takes
        too long.
        """
        if not self._connected or self._ws is None:
            raise ConnectionError("Browser harness is not connected")

        cmd_id = str(uuid.uuid4())
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[cmd_id] = fut

        try:
            await self._send_json({
                "type": "run_script",
                "id": cmd_id,
                "script": script,
            })
            result = await asyncio.wait_for(fut, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(cmd_id, None)
            raise
        except Exception:
            self._pending.pop(cmd_id, None)
            raise

    async def run_script_safe(self, script: str, *, timeout: float = _COMMAND_TIMEOUT) -> dict:
        """Like ``run_script`` but never raises — returns an error dict instead."""
        try:
            return await self.run_script(script, timeout=timeout)
        except ConnectionError:
            return {"status": "not_connected", "message": "Browser harness is not connected. Install and run the harness agent on your computer."}
        except asyncio.TimeoutError:
            return {"status": "timeout", "message": f"Browser script timed out after {timeout}s"}
        except Exception as e:
            logger.warning(f"Browser harness script failed: {e}")
            return {"status": "error", "message": str(e)}

    # ── Internals ─────────────────────────────────────────────────────

    async def _send_json(self, data: dict) -> None:
        if self._ws is None:
            raise ConnectionError("No connected harness")
        try:
            await self._ws.send_json(data)
        except Exception as e:
            await self.disconnect()
            raise ConnectionError(f"Failed to send to harness: {e}")

    @property
    def agent_info(self) -> dict:
        """Human-readable info about the connected harness for the frontend."""
        return {
            "connected": self._connected,
            "available": self.client_available,
            "browser": self._client_info.get("browser"),
            "cdp": self._client_info.get("cdp", False),
            "version": self._client_info.get("harness_version", ""),
        }


# Singleton
bridge = BrowserHarnessBridge()
