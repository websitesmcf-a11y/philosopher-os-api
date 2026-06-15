"""Credential Detector — Detects pasted API keys and secrets.

Scans user input for credential patterns, validates them server-side,
and triggers secure storage flows.
"""

import re
from typing import Optional


CREDENTIAL_PATTERNS = [
    {"pattern": r"AIza[0-9A-Za-z\-_]{35}", "provider": "google_places", "type": "api_key", "label": "Google Places API Key"},
    {"pattern": r"\d{12,20}-[a-zA-Z0-9_\-]{20,40}\.apps\.googleusercontent\.com", "provider": "google_calendar", "type": "oauth_client_id", "label": "Google OAuth Client ID"},
    {"pattern": r"EAACEdEose0cBA[a-zA-Z0-9]{50,}", "provider": "facebook", "type": "access_token", "label": "Meta/Facebook Access Token"},
    {"pattern": r"EAA[A-Za-z0-9]{50,}", "provider": "facebook", "type": "access_token", "label": "Facebook Page Access Token"},
    {"pattern": r"sk-[a-zA-Z0-9]{20,}", "provider": "openai", "type": "api_key", "label": "OpenAI API Key"},
    {"pattern": r"sk-ant-[a-zA-Z0-9]{20,}", "provider": "anthropic", "type": "api_key", "label": "Anthropic API Key"},
    {"pattern": r"sk-[a-f0-9]{32,}", "provider": "deepseek", "type": "api_key", "label": "DeepSeek API Key"},
    {"pattern": r"re_[a-zA-Z0-9]{20,}", "provider": "resend", "type": "api_key", "label": "Resend API Key"},
    {"pattern": r"SG\.[a-zA-Z0-9\-_]{20,}\.[a-zA-Z0-9\-_]{20,}", "provider": "sendgrid", "type": "api_key", "label": "SendGrid API Key"},
    {"pattern": r"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9\.[a-zA-Z0-9\-_]+\.([a-zA-Z0-9\-_]+)", "provider": "supabase", "type": "jwt", "label": "Supabase JWT Token"},
    {"pattern": r"smtp\.[a-zA-Z0-9\-_]+\.[a-zA-Z]+", "provider": "email", "type": "smtp_host", "label": "SMTP Host"},
    {"pattern": r"https?://[a-zA-Z0-9\-_]+\.webhook\.app/[a-zA-Z0-9\-_]+", "provider": "webhook", "type": "url", "label": "Webhook URL"},
]


async def detect_credentials(text: str) -> list[dict]:
    """Scan text for credential patterns and return matches."""
    matches = []
    for cred_def in CREDENTIAL_PATTERNS:
        found = re.findall(cred_def["pattern"], text)
        for match in (found if isinstance(found, list) else [found]):
            full_match = match[0] if isinstance(match, tuple) else match
            if len(str(full_match)) < 10:
                continue
            matches.append({
                "provider": cred_def["provider"],
                "type": cred_def["type"],
                "label": cred_def["label"],
                "masked": mask_credential(str(full_match)),
                "raw_found": True,
            })
    return matches


def mask_credential(cred: str) -> str:
    if len(cred) <= 12:
        return cred[:4] + "****"
    return cred[:6] + "****" + cred[-4:]


async def validate_credential(provider: str, value: str) -> dict:
    """Validate a credential against its provider's API."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            if provider == "google_places":
                resp = await client.get(
                    "https://maps.googleapis.com/maps/api/place/textsearch/json",
                    params={"query": "test", "key": value},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") == "REQUEST_DENIED":
                        return {"valid": False, "error": "Key exists but billing/restrictions may be blocking"}
                    if data.get("status") in ["OK", "ZERO_RESULTS"]:
                        return {"valid": True, "detail": "Google Places key is valid"}
                    return {"valid": False, "error": f"Unexpected: {data.get('status')}"}
                return {"valid": False, "error": f"HTTP {resp.status_code}"}

            if provider == "deepseek":
                resp = await client.post(
                    "https://api.deepseek.com/v1/models",
                    headers={"Authorization": f"Bearer {value}"},
                )
                return {"valid": resp.status_code == 200, "detail": "DeepSeek key is valid" if resp.status_code == 200 else "DeepSeek key rejected"}

            if provider == "openai":
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {value}"},
                )
                return {"valid": resp.status_code == 200, "detail": "OpenAI key is valid" if resp.status_code == 200 else "OpenAI key rejected"}

            return {"valid": True, "detail": f"Stored for {provider}. Full validation requires API call."}
    except Exception as e:
        return {"valid": False, "error": f"Validation failed: {str(e)}"}


async def _get_connection_status(ctx, provider: str) -> bool:
    """Check if an integration is connected for a workspace."""
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
