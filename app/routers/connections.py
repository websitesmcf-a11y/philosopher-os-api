"""Connections router — manage external service integrations from the app."""
import logging
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.crypto import encrypt_dict
from app.core.security import get_current_org, get_current_user
from app.database.models import CalendarEvent, Integration
from app.database.session import get_db
from app.integrations.google_calendar import (
    build_auth_url, exchange_code, get_access_token, list_events as google_list_events,
    create_event as google_create_event,
)
from app.services.connection_service import ConnectionService, PROVIDERS
from app.services.obsidian_sync import sync_vault

logger = logging.getLogger(__name__)
router = APIRouter()


async def _wa_bot_url(db: AsyncSession) -> str:
    result = await db.execute(select(Integration).where(Integration.provider == "whatsapp"))
    row = result.scalar_one_or_none()
    url = (row.config or {}).get("bot_url") if row else None
    return (url or settings.wa_bot_url).rstrip("/")


@router.get("/whatsapp/status")
async def whatsapp_live_status(db: AsyncSession = Depends(get_db)):
    """Live bridge state, polled by the Connections page. Keeps the stored
    integration status in sync so campaign launch checks stay honest."""
    url = await _wa_bot_url(db)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{url}/status")
            data = resp.json()
    except Exception:
        return {"status": "bridge_offline", "connected": False, "phone": None, "qr_available": False}

    # Mirror the live state onto the Integration row (upsert on first link)
    result = await db.execute(select(Integration).where(Integration.provider == "whatsapp"))
    row = result.scalar_one_or_none()
    if data.get("connected"):
        if row is None:
            row = Integration(provider="whatsapp", config={"bot_url": url})
            db.add(row)
        row.status = "connected"
        row.last_error = None
        row.last_checked_at = datetime.utcnow()
    elif row is not None and row.status == "connected":
        row.status = "error"
        row.last_error = "WhatsApp session no longer linked"
        row.last_checked_at = datetime.utcnow()
    await db.flush()

    return data


@router.get("/whatsapp/qr")
async def whatsapp_qr(db: AsyncSession = Depends(get_db)):
    """Current login QR as PNG (404 while not waiting for a scan)."""
    url = await _wa_bot_url(db)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{url}/qr.png")
    except Exception:
        raise HTTPException(status_code=503, detail="WhatsApp bridge is not running")
    if resp.status_code != 200:
        raise HTTPException(status_code=404, detail="No QR available right now")
    return Response(content=resp.content, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/google_calendar/auth-url")
async def google_calendar_auth_url(request: Request, db: AsyncSession = Depends(get_db)):
    """Build the Google OAuth consent URL from saved client credentials."""
    result = await db.execute(select(Integration).where(Integration.provider == "google_calendar"))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=400, detail="Save the client ID and secret first")
    from app.core.crypto import decrypt_dict
    secrets = decrypt_dict(row.credentials_enc or "")
    client_id = secrets.get("client_id") or (row.config or {}).get("client_id", "")
    if not client_id:
        raise HTTPException(status_code=400, detail="Client ID not found — save credentials first")
    redirect_uri = str(request.base_url).rstrip("/") + "/api/v1/connections/google_calendar/callback"
    url = build_auth_url(client_id, redirect_uri)
    return {"auth_url": url, "redirect_uri": redirect_uri}


@router.get("/google_calendar/callback")
async def google_calendar_callback(
    request: Request,
    code: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """OAuth callback: exchange the authorization code for tokens."""
    if error:
        raise HTTPException(status_code=400, detail=f"Google auth error: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    result = await db.execute(select(Integration).where(Integration.provider == "google_calendar"))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=400, detail="No saved Google Calendar credentials")

    from app.core.crypto import decrypt_dict
    secrets = decrypt_dict(row.credentials_enc or "")
    client_id = secrets.get("client_id") or ""
    client_secret = secrets.get("client_secret") or ""
    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Client credentials missing — save them first")

    redirect_uri = str(request.base_url).rstrip("/") + "/api/v1/connections/google_calendar/callback"
    tokens = await exchange_code(client_id, client_secret, code, redirect_uri)

    new_secrets = {
        "client_id": client_id,
        "client_secret": client_secret,
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token", ""),
        "expires_at": (
            datetime.utcnow().isoformat() + "Z"
        ),  # approximate; get_access_token will refresh
    }
    row.credentials_enc = encrypt_dict(new_secrets)
    row.status = "connected"
    row.last_error = None
    row.last_checked_at = datetime.utcnow()
    await db.flush()

    return RedirectResponse(url=str(request.base_url).rstrip("/") + "/connections?google=authorized")


@router.post("/google_calendar/sync")
async def google_calendar_sync(
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Pull Google events now-7d→now+90d, upsert local by external_id;
    push local events without external_id to Google."""
    token = await get_access_token(db)
    if not token:
        raise HTTPException(status_code=400, detail="Google Calendar not connected — authorize first")

    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=7)).isoformat()
    time_max = (now + timedelta(days=90)).isoformat()

    pulled = pushed = 0
    # ── Pull ──
    google_items = await google_list_events(token, time_min, time_max)
    for item in google_items:
        gid = item.get("id")
        if not gid:
            continue
        result = await db.execute(
            select(CalendarEvent).where(CalendarEvent.external_id == gid, CalendarEvent.org_id == org_id)
        )
        existing = result.scalar_one_or_none()
        if existing:
            continue
        start = item.get("start", {}).get("dateTime") or item.get("start", {}).get("date")
        end = item.get("end", {}).get("dateTime") or item.get("end", {}).get("date")
        event = CalendarEvent(
            org_id=org_id,
            external_id=gid,
            title=item.get("summary", "(no title)"),
            description=item.get("description"),
            start_time=start,
            end_time=end,
            location=item.get("location"),
            status="scheduled",
        )
        db.add(event)
        pulled += 1

    # ── Push ──
    result = await db.execute(
        select(CalendarEvent).where(
            CalendarEvent.org_id == org_id,
            CalendarEvent.external_id.is_(None),
            CalendarEvent.start_time >= time_min,
            CalendarEvent.end_time <= time_max,
        )
    )
    for event in result.scalars():
        try:
            created = await google_create_event(
                token, event.title, event.description,
                event.start_time, event.end_time, event.location,
            )
            event.external_id = created.get("id")
            pushed += 1
        except Exception as e:
            logger.warning(f"Failed to push event {event.id} to Google: {e}")

    await db.flush()
    return {"pulled": pulled, "pushed": pushed, "total": pulled + pushed}


@router.post("/obsidian/sync")
async def obsidian_sync(
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Mirror Socrates data (conversations, knowledge, briefings) into the Obsidian vault."""
    result = await db.execute(select(Integration).where(Integration.provider == "obsidian"))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=400, detail="Obsidian vault not configured — save the vault path first")
    vault_path = (row.config or {}).get("vault_path", "").strip()
    if not vault_path:
        raise HTTPException(status_code=400, detail="Vault path not set in saved config")
    try:
        out = await sync_vault(db, vault_path)
        return out
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Obsidian sync failed: {e}")


class ConnectionPayload(BaseModel):
    secrets: dict[str, str] = {}
    config: dict[str, str] = {}


@router.get("")
async def list_connections(db: AsyncSession = Depends(get_db)):
    """All providers with their connection status. Secrets are never returned."""
    service = ConnectionService(db)
    return {"connections": await service.list_connections()}


@router.post("/{provider}")
async def save_connection(
    provider: str,
    payload: ConnectionPayload,
    db: AsyncSession = Depends(get_db),
):
    """Save credentials for a provider; tests them live before storing."""
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
    service = ConnectionService(db)
    try:
        return await service.save_connection(provider, payload.secrets, payload.config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{provider}/test")
async def test_connection(provider: str, db: AsyncSession = Depends(get_db)):
    """Re-test a saved connection against the live service."""
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
    service = ConnectionService(db)
    return await service.test_saved(provider)


@router.delete("/{provider}")
async def delete_connection(provider: str, db: AsyncSession = Depends(get_db)):
    """Disconnect a provider and remove its stored credentials."""
    service = ConnectionService(db)
    deleted = await service.delete_connection(provider)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No saved connection for {provider}")
    return {"provider": provider, "status": "disconnected"}
