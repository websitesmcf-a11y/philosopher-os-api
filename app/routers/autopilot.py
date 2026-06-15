"""Autopilot router — start/stop/status endpoints."""

from fastapi import APIRouter, Request

router = APIRouter()


@router.post("/start")
async def start_autopilot(request: Request):
    """Start the autopilot background loop."""
    autopilot = request.app.state.autopilot
    autopilot.start()
    return {"status": "started", "interval_seconds": autopilot.interval_seconds}


@router.post("/stop")
async def stop_autopilot(request: Request):
    """Stop the autopilot background loop."""
    autopilot = request.app.state.autopilot
    autopilot.stop()
    return {"status": "stopped"}


@router.get("/status")
async def autopilot_status(request: Request):
    """Get autopilot status: running, last_run, actions_taken."""
    autopilot = request.app.state.autopilot
    return autopilot.status
