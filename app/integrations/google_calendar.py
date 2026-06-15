"""Google Calendar integration — OAuth token handling and event CRUD via REST.

No Google SDK: plain httpx against the OAuth2 and Calendar v3 endpoints.
Tokens live encrypted on the google_calendar Integration row.
"""
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt_dict, decrypt_dict
from app.database.models import Integration

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://www.googleapis.com/calendar/v3"
SCOPE = "https://www.googleapis.com/auth/calendar"


def build_auth_url(client_id: str, redirect_uri: str) -> str:
    return f"{AUTH_URL}?" + urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",  # always return a refresh_token
    })


async def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    """Exchange an authorization code for tokens. Raises on failure."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(TOKEN_URL, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        })
        if resp.status_code != 200:
            raise RuntimeError(f"Token exchange failed: {resp.text[:300]}")
        return resp.json()


async def _get_row(db: AsyncSession) -> Integration | None:
    result = await db.execute(select(Integration).where(Integration.provider == "google_calendar"))
    return result.scalar_one_or_none()


async def get_access_token(db: AsyncSession) -> str | None:
    """Valid access token for the connected account, refreshing when expired.

    Returns None when Google Calendar is not connected.
    """
    row = await _get_row(db)
    if row is None:
        return None
    secrets = decrypt_dict(row.credentials_enc or "")
    if not secrets.get("refresh_token"):
        return None

    expires_at = secrets.get("expires_at")
    if secrets.get("access_token") and expires_at:
        if datetime.fromisoformat(expires_at) > datetime.now(timezone.utc) + timedelta(seconds=60):
            return secrets["access_token"]

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(TOKEN_URL, data={
            "client_id": secrets.get("client_id", ""),
            "client_secret": secrets.get("client_secret", ""),
            "refresh_token": secrets["refresh_token"],
            "grant_type": "refresh_token",
        })
        if resp.status_code != 200:
            logger.warning(f"Google token refresh failed: {resp.text[:200]}")
            return None
        data = resp.json()

    secrets["access_token"] = data["access_token"]
    secrets["expires_at"] = (
        datetime.now(timezone.utc) + timedelta(seconds=int(data.get("expires_in", 3600)))
    ).isoformat()
    row.credentials_enc = encrypt_dict(secrets)
    await db.flush()
    return secrets["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _to_google_body(title: str, description: str | None, start, end, location: str | None = None) -> dict:
    return {
        "summary": title,
        "description": description or "",
        "location": location or "",
        "start": {"dateTime": start.isoformat() if hasattr(start, "isoformat") else start},
        "end": {"dateTime": end.isoformat() if hasattr(end, "isoformat") else end},
    }


async def list_events(token: str, time_min: str, time_max: str, max_results: int = 250) -> list[dict]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{API_BASE}/calendars/primary/events",
            headers=_headers(token),
            params={
                "timeMin": time_min, "timeMax": time_max,
                "singleEvents": "true", "orderBy": "startTime", "maxResults": max_results,
            },
        )
        resp.raise_for_status()
        return resp.json().get("items", [])


async def create_event(token: str, title: str, description: str | None, start, end, location: str | None = None) -> dict:
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{API_BASE}/calendars/primary/events",
            headers=_headers(token),
            json=_to_google_body(title, description, start, end, location),
        )
        resp.raise_for_status()
        return resp.json()


async def update_event(token: str, event_id: str, title: str, description: str | None, start, end, location: str | None = None) -> dict:
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.patch(
            f"{API_BASE}/calendars/primary/events/{event_id}",
            headers=_headers(token),
            json=_to_google_body(title, description, start, end, location),
        )
        resp.raise_for_status()
        return resp.json()


async def delete_event(token: str, event_id: str) -> None:
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.delete(
            f"{API_BASE}/calendars/primary/events/{event_id}",
            headers=_headers(token),
        )
        if resp.status_code not in (200, 204, 404, 410):
            resp.raise_for_status()
