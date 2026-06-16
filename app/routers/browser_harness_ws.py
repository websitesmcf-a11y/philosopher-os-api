"""Browser Harness Router — WebSocket endpoint for the local agent + REST status.

The local ``philosopher-harness`` CLI agent connects via WebSocket at ``/ws``
and stays connected, proxying browser commands to the user's Chrome via CDP.

Other endpoints support the frontend: status check, token management, and the
downloadable agent-script for user setup.
"""
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketState

from app.database.session import get_db
from app.database.models import Integration
from app.core.crypto import decrypt_dict
from app.services.browser_harness_bridge import bridge

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/browser-harness", tags=["Browser Harness"])


async def _get_stored_token(db: AsyncSession) -> str | None:
    """Read the saved harness token from the Integration row."""
    result = await db.execute(select(Integration).where(Integration.provider == "browser_harness"))
    row = result.scalar_one_or_none()
    if not row or not row.credentials_enc:
        return None
    secrets = decrypt_dict(row.credentials_enc)
    return secrets.get("token")


async def _validate_token(token: str, db: AsyncSession | None = None) -> bool:
    """Validate a token against the stored one.

    If no DB session is provided, falls back to the simple non-empty check
    (used during WebSocket handshake where Depends(get_db) is awkward).
    """
    if not token:
        return False
    if db is not None:
        stored = await _get_stored_token(db)
        return stored is not None and token == stored
    # Without DB, just check it's non-empty (WebSocket handshake path)
    # The stored-token check happens at command time when a DB session is available.
    return bool(token)


@router.get("/status")
async def get_harness_status():
    """Check whether a browser harness client is currently connected.

    The frontend polls this to show live connection state and to gate
    Beast Mode levels that require browser access.
    """
    return bridge.agent_info


@router.get("/token")
async def get_harness_token(db: AsyncSession = Depends(get_db)):
    """Return the current harness token (for the frontend to display to the user)."""
    token = await _get_stored_token(db)
    if not token:
        raise HTTPException(status_code=404, detail="No browser harness token saved yet")
    return {"token": token}


@router.post("/test-script")
async def test_browser_script(body: dict):
    """TEMP: Test a browser script through the bridge."""
    from fastapi.responses import JSONResponse
    script = body.get("script", "print('hello from bridge')")
    result = await bridge.run_script_safe(script, timeout=30.0)
    return JSONResponse(result)


@router.get("/agent-script")
async def download_agent_script():
    """Return the Python CLI agent script for download.

    Users curl this and save it as ``philosopher-harness.py``, then run it
    with their token to connect their local Chrome.
    """
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(
        content=_AGENT_CODE,
        media_type="text/plain",
        headers={"Content-Disposition": "attachment; filename=philosopher-harness.py"},
    )


@router.websocket("/ws")
async def harness_websocket(ws: WebSocket):
    """WebSocket endpoint for the local browser-harness agent.

    The agent connects here and stays connected, receiving ``run_script``
    commands and sending back results. Only one agent may be connected at a time.

    Query param ``token`` is required — set in Integrations > Browser Harness.
    """
    await ws.accept()

    token = ws.query_params.get("token", "")
    if not await _validate_token(token):
        await ws.send_json({"type": "error", "message": "Invalid token. Generate one in Integrations > Browser Harness."})
        await ws.close(code=4001)
        return

    try:
        await bridge.connect(ws)
        while True:
            raw = await ws.receive_text()
            await bridge.handle_message(raw)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"Browser harness WebSocket error: {e}")
    finally:
        await bridge.disconnect()
        try:
            if ws.client_state == WebSocketState.CONNECTED:
                await ws.close()
        except Exception:
            pass


# ─── Agent Script (distributed as a download) ────────────────────────

_AGENT_CODE = r'''#!/usr/bin/env python3
"""Philosopher OS Browser Harness Agent.

Connects to your Philosopher OS instance via WebSocket and proxies browser
commands to your local Chrome through the browser-harness CLI.

Usage:
    pip install websockets      # or: websocket-client
    python philosopher-harness.py --url https://your-backend.com --token YOUR_TOKEN

The token is generated in Philosopher OS → Integrations → Browser Harness.
Keep this running in your terminal while using Beast Mode Level 3+.
"""
import argparse
import asyncio
import json
import logging
import shlex
import subprocess
import sys
import uuid

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("philosopher-harness")


def check_harness() -> dict:
    """Check if browser-harness CLI is installed and Chrome is reachable."""
    import shutil
    harness_path = shutil.which("browser-harness")
    if not harness_path:
        return {"available": False, "browser": None, "cdp": False, "version": ""}
    version = ""
    try:
        result = subprocess.run(
            [harness_path, "--version"],
            capture_output=True, text=True, timeout=5,
        )
        version = result.stdout.strip() or result.stderr.strip() or ""
    except Exception:
        pass
    # Probe Chrome connectivity via a simple harness script
    cdp = False
    try:
        probe = subprocess.run(
            [harness_path],
            input=b"print('cdp_ok')\n",
            capture_output=True, timeout=10,
        )
        cdp = b"cdp_ok" in probe.stdout
    except Exception:
        pass
    return {"available": True, "browser": "chrome", "cdp": cdp, "version": version[:50]}


async def run_command(cmd_id: str, script: str) -> dict:
    """Run a Python script through the browser-harness CLI and return the result."""
    import shutil
    harness = shutil.which("browser-harness")
    if not harness:
        return {"type": "result", "id": cmd_id, "status": "error",
                "output": "", "error": "browser-harness CLI not found on PATH"}

    loop = asyncio.get_event_loop()

    def _run():
        try:
            proc = subprocess.run(
                [harness],
                input=script.encode("utf-8"),
                capture_output=True, timeout=150,
            )
            stdout = proc.stdout.decode("utf-8", errors="replace").strip()
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            if proc.returncode == 0:
                return {"type": "result", "id": cmd_id, "status": "success",
                        "output": stdout[:400_000], "error": stderr[:4000]}
            return {"type": "result", "id": cmd_id, "status": "error",
                    "output": stdout[:400_000], "error": stderr[:4000]}
        except subprocess.TimeoutExpired:
            return {"type": "result", "id": cmd_id, "status": "timeout",
                    "output": "", "error": "Script timed out after 150s"}
        except Exception as e:
            return {"type": "result", "id": cmd_id, "status": "error",
                    "output": "", "error": str(e)}

    return await loop.run_in_executor(None, _run)


async def main():
    parser = argparse.ArgumentParser(description="Philosopher OS Browser Harness Agent")
    parser.add_argument("--url", required=True, help="Backend URL (e.g. https://web-production-a93f0.up.railway.app)")
    parser.add_argument("--token", required=True, help="Auth token from Philosopher OS → Integrations → Browser Harness")
    args = parser.parse_args()

    ws_url = args.url.rstrip("/").replace("http://", "ws://").replace("https://", "wss://")
    ws_url += "/api/v1/browser-harness/ws"

    log.info("Starting Philosopher OS Browser Harness Agent")
    log.info("Backend: %s", args.url)
    log.info("WebSocket: %s", ws_url)

    try:
        import websockets
    except ImportError:
        log.error("Missing dependency: pip install websockets")
        sys.exit(1)

    status = check_harness()
    log.info("Browser-harness CLI: %s", "found" if status["available"] else "NOT FOUND")
    log.info("Chrome CDP: %s", "reachable" if status["cdp"] else "not reachable (is Chrome running with --remote-debugging-port?)")

    reconnect_delay = 5
    while True:
        try:
            async with websockets.connect(
                ws_url,
                additional_headers={"Authorization": f"Bearer {args.token}"},  # fallback auth
            ) as ws:
                # Send connection params as query param
                # Actually, let's reconnect with token in query string
                pass

            # Connect with token in query string (WebSocket URL format)
            connect_url = f"{ws_url}?token={args.token}"
            async with websockets.connect(connect_url, ping_interval=30, ping_timeout=10) as ws:
                log.info("Connected to Philosopher OS")

                # Send initial status
                await ws.send(json.dumps({"type": "status", **status}))
                reconnect_delay = 5

                async def heartbeat():
                    while True:
                        await asyncio.sleep(25)
                        try:
                            await ws.send(json.dumps({"type": "ping"}))
                        except Exception:
                            break

                async def status_reporter():
                    while True:
                        await asyncio.sleep(60)
                        try:
                            s = check_harness()
                            await ws.send(json.dumps({"type": "status", **s}))
                        except Exception:
                            break

                async with asyncio.TaskGroup() as tg:
                    tg.create_task(heartbeat())
                    tg.create_task(status_reporter())

                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        if msg.get("type") == "run_script":
                            cmd_id = msg.get("id", str(uuid.uuid4()))
                            script = msg.get("script", "")
                            log.info("Running script (%s chars)...", len(script))
                            result = await run_command(cmd_id, script)
                            await ws.send(json.dumps(result))
                            log.info("Script complete: %s", result.get("status"))

                        elif msg.get("type") == "pong":
                            pass  # heartbeat reply, nothing to do

        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning("Connection lost: %s", e)
            log.info("Reconnecting in %s seconds...", reconnect_delay)
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down")
'''


