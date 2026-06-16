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
"""Philosopher OS Browser Harness Agent — persistent daemon.

Connects your local Chrome to Philosopher OS via WebSocket so AI agents
can browse the web on your behalf.

Features:
  - Auto-launches Chrome with --remote-debugging-port if not already running
  - Reconnects instantly (< 500ms) after network blips or Railway deploys
  - Can run as a background daemon (Windows: --install adds boot startup)
  - Logs to file in daemon mode; visible output in foreground mode

Usage:
  # Foreground (default) — shows output in terminal:
  python philosopher-harness.py --url https://web-production-a93f0.up.railway.app --token YOUR_TOKEN

  # Install as auto-start service (runs on PC boot):
  python philosopher-harness.py --url https://web-production-a93f0.up.railway.app --token YOUR_TOKEN --install

  # Uninstall auto-start:
  python philosopher-harness.py --uninstall
"""
import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
import uuid

# Fix Windows CP1252 stdout so non-ASCII chars don't crash logging
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import websockets
except ImportError:
    print("ERROR: Missing 'websockets' package. Run: python -m pip install websockets")
    sys.exit(1)

log = logging.getLogger("philosopher-harness")

# ── Constants ──────────────────────────────────────────────────────────

HEARTBEAT_INTERVAL = 15  # seconds between pings
RECONNECT_DELAYS = [0.2, 0.5, 1.0, 2.0, 4.0]  # retry backoff in seconds
MAX_RECONNECT_DELAY = 8.0
CHROME_PORT = 9222


# ── Chrome launcher ────────────────────────────────────────────────────

def find_chrome_path() -> str | None:
    """Find Chrome/Chromium executable path on Windows."""
    candidates = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles%\Chromium\Application\chrome.exe"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    # Try PATH
    import shutil
    return shutil.which("chrome") or shutil.which("chromium") or shutil.which("google-chrome")


def is_chrome_running_with_debug() -> bool:
    """Quick check if Chrome is already listening on the debug port."""
    try:
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", CHROME_PORT, timeout=3)
        c.request("GET", "/json/version")
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status == 200
    except Exception:
        return False


def launch_chrome() -> bool:
    """Launch Chrome with remote debugging in the background. Returns True on success."""
    chrome = find_chrome_path()
    if not chrome:
        log.warning("Chrome not found on this system — cannot auto-launch")
        return False
    try:
        subprocess.Popen(
            [chrome, f"--remote-debugging-port={CHROME_PORT}"],
            close_fds=True,
        )
        log.info("Launched Chrome with --remote-debugging-port=%s", CHROME_PORT)
        return True
    except Exception as e:
        log.warning("Failed to launch Chrome: %s", e)
        return False


def ensure_chrome() -> bool:
    """Make sure Chrome is running with remote debugging. Returns True if available."""
    if is_chrome_running_with_debug():
        return True
    # Give it a moment to start if it was just launched
    launch_chrome()
    for _ in range(15):
        time.sleep(1)
        if is_chrome_running_with_debug():
            return True
    return False


# ── Boot persistence (Windows) ─────────────────────────────────────────

def _startup_script_path() -> str:
    """Return path to the auto-start batch file."""
    appdata = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    return os.path.join(appdata, "PhilosopherOS", "harness-start.bat")


def install_startup(url: str, token: str) -> None:
    """Create a startup batch file and register it to run on boot."""
    script_path = _startup_script_path()
    os.makedirs(os.path.dirname(script_path), exist_ok=True)

    # Write a batch file that launches the agent hidden
    self_path = os.path.abspath(sys.argv[0])
    batch = f"""@echo off
start /B "" "{sys.executable}" "{self_path}" --url "{url}" --token "{token}" --daemon
"""
    with open(script_path, "w") as f:
        f.write(batch)

    # Register in Windows startup folder
    startup_folder = os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~")),
        r"Microsoft\Windows\Start Menu\Programs\Startup",
    )
    link_path = os.path.join(startup_folder, "PhilosopherOS-Harness.bat")
    with open(link_path, "w") as f:
        f.write(f'call "{script_path}"\n')

    log.info("Installed auto-start: %s", link_path)
    log.info("The harness will auto-connect every time you log into Windows.")
    log.info("To remove: run with --uninstall")


def uninstall_startup() -> None:
    """Remove auto-start registration."""
    folders = [
        os.environ.get("APPDATA", ""),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    removed = False
    for base in folders:
        if not base:
            continue
        startup = os.path.join(base, r"Microsoft\Windows\Start Menu\Programs\Startup")
        for name in ("PhilosopherOS-Harness.bat", "PhilosopherOS-Harness.lnk"):
            p = os.path.join(startup, name)
            if os.path.isfile(p):
                os.remove(p)
                removed = True
        # Remove the script dir
        script_dir = os.path.join(base, "PhilosopherOS")
        if os.path.isdir(script_dir):
            try:
                import shutil
                shutil.rmtree(script_dir)
            except Exception:
                pass
    if removed:
        log.info("Auto-start removed")
    else:
        log.info("No auto-start registration found")


# ── Browser harness runner ─────────────────────────────────────────────

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


# ── WebSocket session ──────────────────────────────────────────────────

async def run_session(url: str, token: str) -> None:
    """Run one WebSocket session with heartbeating, status reporting, and Chrome health."""
    status = check_harness()
    # Ensure Chrome is running with CDP
    if not status["cdp"]:
        log.info("Chrome CDP not reachable — attempting to auto-launch...")
        if ensure_chrome():
            status = check_harness()
            log.info("Chrome CDP: %s", "reachable" if status["cdp"] else "still not reachable")
        else:
            log.warning("Could not auto-launch Chrome. Is it installed?")

    ws_url = url.rstrip("/").replace("http://", "ws://").replace("https://", "wss://")
    ws_url += "/api/v1/browser-harness/ws"

    connect_url = f"{ws_url}?token={token}"
    log.info("Connecting to %s", connect_url[:80] + "...")

    async with websockets.connect(connect_url, ping_interval=15, ping_timeout=8) as ws:
        log.info("Connected OK")

        # Send initial status
        await ws.send(json.dumps({"type": "status", **status}))

        async def heartbeat():
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                try:
                    await ws.send(json.dumps({"type": "ping"}))
                except Exception:
                    break

        async def status_reporter():
            while True:
                await asyncio.sleep(45)
                try:
                    s = check_harness()
                    # If Chrome died, try reviving
                    if not s["cdp"]:
                        ensure_chrome()
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
                    pass  # heartbeat reply


# ── Main ───────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(
        description="Philosopher OS Browser Harness Agent — persistent daemon"
    )
    parser.add_argument("--url", help="Backend URL (e.g. https://web-production-a93f0.up.railway.app)")
    parser.add_argument("--token", help="Auth token from Integrations → Browser Harness")
    parser.add_argument("--install", action="store_true", help="Register to auto-start on boot")
    parser.add_argument("--uninstall", action="store_true", help="Remove auto-start registration")
    parser.add_argument("--daemon", action="store_true", help="Run in background (no console output)")
    args = parser.parse_args()

    # Handle --uninstall first
    if args.uninstall:
        uninstall_startup()
        return

    # Handle --install
    if args.install:
        if not args.url or not args.token:
            print("ERROR: --url and --token are required with --install")
            sys.exit(1)
        install_startup(args.url, args.token)
        print("Auto-start installed. The harness will connect on next login.")
        print("To start now, run without --install (or reboot).")
        return

    # Must have url + token for normal mode
    if not args.url or not args.token:
        parser.print_help()
        sys.exit(1)

    # Set up logging
    log_level = logging.INFO
    if args.daemon:
        log_path = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "PhilosopherOS", "harness.log",
        )
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        handler = logging.FileHandler(log_path)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logging.basicConfig(level=log_level, handlers=[handler])
    else:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logging.basicConfig(level=log_level, handlers=[handler])

    log.info("Starting Philosopher OS Browser Harness Agent")
    log.info("Backend: %s", args.url)
    if args.daemon:
        log.info("Mode: background daemon")

    # Main reconnect loop — instant first retry, then backoff
    delay_idx = 0
    while True:
        try:
            await run_session(args.url, args.token)
            # clean disconnect (unlikely) — reset backoff
            delay_idx = 0
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning("Disconnected: %s", e)
            delay = RECONNECT_DELAYS[delay_idx] if delay_idx < len(RECONNECT_DELAYS) else MAX_RECONNECT_DELAY
            delay_idx = min(delay_idx + 1, len(RECONNECT_DELAYS))
            log.info("Reconnecting in %.1fs...", delay)
            await asyncio.sleep(delay)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down")
'''


