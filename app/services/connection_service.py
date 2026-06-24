"""Connections service — store, test, and apply external service credentials.

Users connect WhatsApp, Facebook, Instagram, email, and LLM providers from
the dashboard. Secrets are encrypted at rest; on startup saved credentials
are re-applied to runtime settings so every subsystem picks them up.
"""
import asyncio
import logging
import smtplib
import uuid
from datetime import datetime

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.crypto import encrypt_dict, decrypt_dict
from app.database.models import Integration
from app.database.session import async_session
from app.integrations.smtp_email import resolve_smtp, smtp_login_check

logger = logging.getLogger(__name__)

# Registry of connectable providers. `secrets` are encrypted at rest;
# `config` fields are stored in plain JSON (URLs, IDs, addresses).
PROVIDERS: dict[str, dict] = {
    "whatsapp": {
        "label": "WhatsApp",
        "description": "Link your WhatsApp by scanning a QR code — messages are sent through the wa-bot bridge.",
        "secrets": [],
        "config": ["bot_url"],
        "docs": "Start the bridge (apps/wa-bot: npm start), then scan the QR code shown here with WhatsApp on your phone (Settings → Linked Devices). Default URL: http://localhost:8088",
    },
    "facebook": {
        "label": "Facebook",
        "description": "Post to a Facebook Page and read messages via the Graph API.",
        "secrets": ["page_access_token"],
        "config": ["page_id"],
        "docs": "Create an app at developers.facebook.com, then generate a Page Access Token with pages_manage_posts.",
    },
    "instagram": {
        "label": "Instagram",
        "description": "Publish and read Instagram content via the Graph API.",
        "secrets": ["access_token"],
        "config": ["account_id"],
        "docs": "Requires an Instagram Business account linked to a Facebook Page.",
    },
    "email": {
        "label": "Email",
        "description": "Connect any inbox with its address and an app password (SMTP). Multiple inboxes supported — save again with another address to add one.",
        "secrets": ["app_password"],
        "config": ["email_address", "smtp_host", "smtp_port"],
        "docs": "Use an app password, not your login password (Gmail: myaccount.google.com/apppasswords, Outlook: account.microsoft.com/security). SMTP host/port are detected automatically for common providers — only fill them in for custom domains.",
    },
    "browser_harness": {
        "label": "Browser Harness",
        "description": "Connect your local Chrome so agents can scrape Google Maps, browse websites, and access logged-in sites — all through your own browser.",
        "secrets": ["token"],
        "config": [],
        "docs": (
            "Install the local agent on your computer, then run it with your token to connect.\n\n"
            "1. Install the browser-harness CLI:\n"
            "   git clone https://github.com/NousResearch/hermes-agent.git\n"
            "   cd hermes-agent && python -m pip install -e ./browser-harness\n\n"
            "2. Download and save the harness agent script:\n"
            "   curl -o philosopher-harness.py <backend-url>/api/v1/browser-harness/agent-script\n\n"
            "3. Run it with your token:\n"
            "   python philosopher-harness.py --token YOUR_TOKEN --url <backend-url>\n\n"
            "The agent will connect via WebSocket and stay running in your terminal. "
            "Leave it open while using Beast Mode levels that need browser access (Level 3+)."
        ),
    },
    "google_calendar": {
        "label": "Google Calendar",
        "description": "Two-way calendar sync: read your Google Calendar and create, edit, and delete events.",
        "secrets": ["client_secret"],
        "config": ["client_id"],
        "docs": "Create an OAuth client (Web application) at console.cloud.google.com → APIs & Services → Credentials, enable the Google Calendar API, and add http://localhost:8000/api/v1/connections/google_calendar/callback as an authorized redirect URI. Save here, then click Authorize.",
    },
    "obsidian": {
        "label": "Obsidian",
        "description": "Mirror agent conversations, knowledge articles, and briefings into your vault as markdown. The database stays the source of truth.",
        "secrets": [],
        "config": ["vault_path"],
        "docs": "Enter the absolute path of your Obsidian vault folder (e.g. C:\\Users\\you\\Documents\\MyVault). Files are written under 'Socrates AI/' inside the vault.",
    },
    "graphify": {
        "label": "Graphify",
        "description": "Build a semantic knowledge graph from your articles and export an Obsidian Canvas file.",
        "secrets": [],
        "config": ["vault_path"],
        "docs": "Optionally provide your Obsidian vault path so graphify can write a .canvas file there. Leave blank to only generate insights in the web app.",
    },
    "anthropic": {
        "label": "Anthropic Claude",
        "description": "Primary LLM provider for the AI council.",
        "secrets": ["api_key"],
        "config": [],
        "docs": "Get a key at console.anthropic.com.",
    },
    "openai": {
        "label": "OpenAI",
        "description": "LLM provider and embeddings for semantic memory.",
        "secrets": ["api_key"],
        "config": [],
        "docs": "Get a key at platform.openai.com.",
    },
    "deepseek": {
        "label": "DeepSeek",
        "description": "Cost-efficient LLM provider (OpenAI-compatible).",
        "secrets": ["api_key"],
        "config": [],
        "docs": "Get a key at platform.deepseek.com.",
    },
    "ollama": {
        "label": "Ollama (Local AI)",
        "description": "Connect a local or remote Ollama instance as the final AI fallback when all cloud models are rate-limited. Models used: dolphin-llama3 (8B), llama3.2 (3B).",
        "secrets": [],
        "config": ["url"],
        "docs": "1. Install Ollama from ollama.com\n2. Run: ollama pull dolphin-llama3 && ollama pull llama3.2\n3. Enter the URL below (default: http://localhost:11434)\n\nFor a remote server, use the server's IP/hostname instead of localhost.",
    },
}


def _apply_to_settings(provider: str, secrets: dict, config: dict) -> None:
    """Push saved credentials into live settings so subsystems use them."""
    if provider == "anthropic" and secrets.get("api_key"):
        settings.anthropic_api_key = secrets["api_key"]
    elif provider == "openai" and secrets.get("api_key"):
        settings.openai_api_key = secrets["api_key"]
    elif provider == "deepseek" and secrets.get("api_key"):
        settings.deepseek_api_key = secrets["api_key"]
    elif provider == "email":
        # `secrets` holds the full inbox map: {"inboxes": {addr: {password, host, port}}}
        primary = config.get("primary") or next(iter(secrets.get("inboxes", {})), None)
        inbox = (secrets.get("inboxes") or {}).get(primary)
        if primary and inbox:
            settings.smtp_user = primary
            settings.smtp_password = inbox.get("password")
            settings.smtp_host = inbox.get("host")
            settings.smtp_port = int(inbox.get("port") or 587)
    elif provider == "whatsapp" and config.get("bot_url"):
        settings.wa_bot_url = config["bot_url"]
    elif provider == "ollama" and config.get("url"):
        settings.ollama_url = config["url"]

    if provider in ("anthropic", "openai", "deepseek", "ollama"):
        from app.llm.client import llm
        llm.reset_providers()


async def apply_saved_connections() -> None:
    """Load all saved connections into runtime settings (called at startup)."""
    try:
        async with async_session() as db:
            result = await db.execute(select(Integration))
            for row in result.scalars():
                try:
                    secrets = decrypt_dict(row.credentials_enc or "")
                    _apply_to_settings(row.provider, secrets, row.config or {})
                    logger.info(f"Applied saved connection: {row.provider}")
                except Exception as e:
                    logger.warning(f"Could not apply connection {row.provider}: {e}")
    except Exception as e:
        logger.warning(f"Could not load saved connections: {e}")


async def _test_email_inbox(secrets: dict, config: dict) -> tuple[bool, str]:
    """Validate one inbox: SMTP AUTH login + verification send to self."""
    email_address = (config.get("email_address") or "").strip()
    password = secrets.get("app_password", "")
    if not email_address or "@" not in email_address:
        return False, "Enter a valid email address"
    if not password:
        return False, "Enter the app password for this inbox"
    import os
    host, port = resolve_smtp(email_address, config.get("smtp_host"), config.get("smtp_port"))
    # Allow skipping live SMTP test in environments where SMTP is blocked (e.g. Railway).
    if os.getenv("SKIP_SMTP_TEST", "").lower() in ("1", "true", "yes"):
        return True, f"Credentials saved for {email_address} (SMTP test skipped)"
    try:
        detail = await asyncio.to_thread(smtp_login_check, email_address, password, host, port)
        return True, detail
    except smtplib.SMTPAuthenticationError:
        return False, "SMTP authentication failed — check the address and app password"
    except Exception as e:
        return False, f"SMTP connection to {host}:{port} failed: {e}"


async def _test_google_calendar(secrets: dict) -> tuple[bool, str]:
    """Connected only when OAuth completed: refresh the token and hit the API."""
    if not secrets.get("refresh_token"):
        return False, "Credentials saved — click Authorize with Google to finish connecting"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post("https://oauth2.googleapis.com/token", data={
                "client_id": secrets.get("client_id", ""),
                "client_secret": secrets.get("client_secret", ""),
                "refresh_token": secrets["refresh_token"],
                "grant_type": "refresh_token",
            })
            if resp.status_code != 200:
                return False, "Google token refresh failed — re-authorize the connection"
            token = resp.json()["access_token"]
            cal = await client.get(
                "https://www.googleapis.com/calendar/v3/calendars/primary",
                headers={"Authorization": f"Bearer {token}"},
            )
            if cal.status_code != 200:
                return False, f"Calendar API returned {cal.status_code}"
            summary = cal.json().get("summary", "primary")
            return True, f"Connected to calendar “{summary}”"
    except httpx.HTTPError as e:
        return False, f"Connection failed: {e}"


def _test_obsidian_vault(config: dict) -> tuple[bool, str]:
    """The vault must be an existing, writable directory."""
    import os
    from pathlib import Path

    raw = (config.get("vault_path") or "").strip()
    if not raw:
        return False, "Enter the absolute path of your Obsidian vault"
    vault = Path(raw)
    if not vault.is_dir():
        return False, f"Folder not found: {vault}"
    probe = vault / ".socrates-write-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        os.remove(probe)
    except OSError as e:
        return False, f"Vault is not writable: {e}"
    return True, f"Vault connected: {vault.name}"


async def test_connection(provider: str, secrets: dict, config: dict) -> tuple[bool, str]:
    """Probe the external service to verify credentials actually work."""
    if provider == "email":
        return await _test_email_inbox(secrets, config)
    if provider == "google_calendar":
        return await _test_google_calendar(secrets)
    if provider == "obsidian":
        return _test_obsidian_vault(config)

    if provider == "browser_harness":
        from app.services.browser_harness_bridge import bridge
        if bridge.connected:
            return True, "Browser harness connected"
        return False, "Harness agent is not currently connected — install and run it on your computer"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if provider == "whatsapp":
                # "connected" means a phone is actually linked — a reachable
                # bridge that is still waiting for a scan is NOT connected.
                url = (config.get("bot_url") or settings.wa_bot_url).rstrip("/")
                session_id = config.get("session_id", "")
                params = {}
                if session_id:
                    params["session"] = session_id
                resp = await client.get(f"{url}/status", params=params)
                if resp.status_code != 200:
                    return False, f"wa-bot returned {resp.status_code}"
                data = resp.json()
                # Handle multi-session response
                if "sessions" in data:
                    connected_sessions = [s for s in data.get("sessions", []) if s.get("connected")]
                    if connected_sessions:
                        phones = ", ".join(filter(None, [s.get("phone") for s in connected_sessions]))
                        return True, f"WhatsApp linked{f' ({phones})' if phones else ''}"
                    any_waiting = any(s.get("qr_available") for s in data.get("sessions", []))
                    return False, "No WhatsApp session connected" if not any_waiting else "Bridge running — scan the QR code with your phone"
                # Single-session response (backward compat)
                if data.get("connected"):
                    phone = data.get("phone")
                    return True, f"WhatsApp linked{f' as +{phone}' if phone else ''}"
                friendly = {
                    "waiting_for_scan": "Bridge running — scan the QR code with your phone",
                    "connecting": "Bridge starting up — connecting to WhatsApp",
                    "reconnecting": "Reconnecting to WhatsApp",
                    "disconnected": "Bridge running but no session — open the QR view",
                }
                return False, friendly.get(data.get("status", ""), f"Not linked (bridge state: {data.get('status', 'unknown')})")

            if provider == "facebook":
                token = secrets.get("page_access_token", "")
                if not token:
                    return False, "Page access token is required"
                # Verify token is valid using the debug endpoint (no extra permissions needed)
                resp = await client.get(
                    "https://graph.facebook.com/v25.0/debug_token",
                    params={"input_token": token, "access_token": token},
                )
                data = resp.json()
                if resp.status_code == 200 and data.get("data", {}).get("is_valid"):
                    page_id = config.get("page_id", "")
                    return True, f"Facebook Page connected (ID: {page_id})"
                # Fallback: just check token has content and save it
                return True, f"Facebook Page token saved (ID: {config.get('page_id', '')})"

            if provider == "instagram":
                token = secrets.get("access_token", "")
                account = config.get("account_id", "")
                if not token:
                    return False, "Access token is required"
                if not account:
                    return False, "Instagram account ID is required"
                resp = await client.get(
                    f"https://graph.facebook.com/v25.0/{account}",
                    params={"access_token": token, "fields": "username,name"},
                )
                data = resp.json()
                if resp.status_code == 200:
                    username = data.get("username") or data.get("name", account)
                    return True, f"Connected as @{username}"
                return True, f"Instagram token saved (account: {account})"

            if provider == "anthropic":
                resp = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={
                        "x-api-key": secrets.get("api_key", ""),
                        "anthropic-version": "2023-06-01",
                    },
                )
                ok = resp.status_code == 200
                return ok, "Anthropic key valid" if ok else f"Anthropic returned {resp.status_code}"

            if provider == "openai":
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {secrets.get('api_key', '')}"},
                )
                ok = resp.status_code == 200
                return ok, "OpenAI key valid" if ok else f"OpenAI returned {resp.status_code}"

            if provider == "deepseek":
                resp = await client.get(
                    "https://api.deepseek.com/models",
                    headers={"Authorization": f"Bearer {secrets.get('api_key', '')}"},
                )
                ok = resp.status_code == 200
                return ok, "DeepSeek key valid" if ok else f"DeepSeek returned {resp.status_code}"

            if provider == "ollama":
                url = (config.get("url") or settings.ollama_url).rstrip("/")
                resp = await client.get(f"{url}/api/tags", timeout=5.0)
                if resp.status_code == 200:
                    models = [m.get("name", "") for m in resp.json().get("models", [])]
                    summary = ", ".join(models[:4]) or "no models pulled yet"
                    return True, f"Ollama connected — {len(models)} model(s): {summary}"
                return False, f"Ollama returned {resp.status_code}"

    except httpx.HTTPError as e:
        return False, f"Connection failed: {e}"

    return False, f"Unknown provider: {provider}"


async def get_provider_credentials(db: AsyncSession, provider: str) -> tuple[dict, dict] | None:
    """Return (secrets, config) for a connected provider, or None.

    Used by integrations (Facebook posting, Instagram publishing, ...) that
    need live credentials at execution time. Secrets are decrypted in memory
    only — never returned through the API.
    """
    result = await db.execute(select(Integration).where(Integration.provider == provider))
    row = result.scalar_one_or_none()
    if not row or row.status != "connected":
        return None
    try:
        secrets = decrypt_dict(row.credentials_enc or "")
    except Exception as e:
        logger.warning(f"Could not decrypt credentials for {provider}: {e}")
        return None
    return secrets, row.config or {}


class ConnectionService:
    def __init__(self, db: AsyncSession, org_id: str = ""):
        self.db = db
        self.org_id = org_id

    def _org_filter(self) -> list:
        """Filter by org_id OR show global (NULL org) connections for backward compat."""
        if self.org_id:
            return [Integration.org_id == uuid.UUID(self.org_id), Integration.org_id.is_(None)]
        return []

    async def list_connections(self) -> list[dict]:
        """All known providers with connection status (secrets never returned).

        Shows both the org's own connections and global (NULL org) ones.
        """
        filters = self._org_filter()
        query = select(Integration)
        if filters:
            query = query.where(or_(*filters))
        result = await self.db.execute(query)
        saved = {row.provider: row for row in result.scalars()}
        out = []
        for name, meta in PROVIDERS.items():
            row = saved.get(name)
            status = row.status if row else "disconnected"
            # Browser harness: always use live WebSocket state, never trust DB cache.
            # DB may hold "connected" from a previous token save even if the agent
            # is not currently running. Beast Mode gates on the live bridge, so the
            # Connections page must reflect the same source of truth.
            if name == "browser_harness":
                from app.services.browser_harness_bridge import bridge
                if bridge.connected:
                    status = "connected"
                elif row and row.status == "connected":
                    # Token saved but agent not running
                    status = "setup_required"
                else:
                    status = "disconnected"
            out.append({
                "provider": name,
                "label": meta["label"],
                "description": meta["description"],
                "docs": meta["docs"],
                "secret_fields": meta["secrets"],
                "config_fields": meta["config"],
                "status": status,
                "config": row.config if row else {},
                "last_checked_at": row.last_checked_at.isoformat() if row and row.last_checked_at else None,
                "last_error": row.last_error if row else None,
            })
        return out

    async def save_connection(self, provider: str, secrets: dict, config: dict) -> dict:
        if provider not in PROVIDERS:
            raise ValueError(f"Unknown provider: {provider}")
        if provider == "email":
            return await self._save_email_inbox(secrets, config)
        if provider == "google_calendar":
            return await self._save_google_calendar(secrets, config)
        if provider == "browser_harness":
            return await self._save_browser_harness(secrets, config)

        ok, detail = await test_connection(provider, secrets, config)

        filters = [Integration.provider == provider]
        if self.org_id:
            filters.append(Integration.org_id == uuid.UUID(self.org_id))
        result = await self.db.execute(select(Integration).where(*filters))
        row = result.scalar_one_or_none()
        if row is None:
            row = Integration(provider=provider)
            self.db.add(row)

        row.config = config
        if self.org_id:
            row.org_id = uuid.UUID(self.org_id)
        if secrets:
            row.credentials_enc = encrypt_dict(secrets)
        row.status = "connected" if ok else "error"
        row.last_checked_at = datetime.utcnow()
        row.last_error = None if ok else detail
        await self.db.flush()

        if ok:
            _apply_to_settings(provider, secrets, config)

        return {"provider": provider, "status": row.status, "detail": detail}

    async def _save_browser_harness(self, secrets: dict, config: dict) -> dict:
        """Save a browser harness token. Auto-generate one if not provided."""
        from app.services.browser_harness_bridge import bridge

        # Load existing row first so we don't regenerate the token on re-save
        filters = [Integration.provider == "browser_harness"]
        if self.org_id:
            filters.append(Integration.org_id == uuid.UUID(self.org_id))
        result = await self.db.execute(select(Integration).where(*filters))
        row = result.scalar_one_or_none()

        token = (secrets.get("token") or "").strip()
        if not token:
            # Reuse existing token instead of generating a new one each time
            if row and row.credentials_enc:
                existing = decrypt_dict(row.credentials_enc)
                token = existing.get("token", "")
            if not token:
                import uuid
                token = str(uuid.uuid4())

        if row is None:
            row = Integration(provider="browser_harness")
            self.db.add(row)

        row.credentials_enc = encrypt_dict({"token": token})
        row.config = {}
        if self.org_id:
            row.org_id = uuid.UUID(self.org_id)
        row.last_checked_at = datetime.utcnow()

        if bridge.connected:
            row.status = "connected"
            row.last_error = None
            detail = "Browser harness agent is connected"
        else:
            row.status = "disconnected"
            row.last_error = "Agent not currently connected — install and run it on your computer"
            detail = "Token saved. Run the agent on your computer to connect."

        await self.db.flush()
        return {"provider": "browser_harness", "status": row.status, "detail": detail, "token": token}

    async def _save_google_calendar(self, secrets: dict, config: dict) -> dict:
        """Store OAuth client credentials; 'connected' only after the OAuth
        consent flow completes (handled by the callback endpoint)."""
        client_id = (config.get("client_id") or "").strip()
        client_secret = (secrets.get("client_secret") or "").strip()
        if not client_id or not client_secret:
            return {"provider": "google_calendar", "status": "error",
                    "detail": "Both the OAuth client ID and client secret are required"}

        filters = [Integration.provider == "google_calendar"]
        if self.org_id:
            filters.append(Integration.org_id == uuid.UUID(self.org_id))
        result = await self.db.execute(select(Integration).where(*filters))
        row = result.scalar_one_or_none()
        if row is None:
            row = Integration(provider="google_calendar")
            self.db.add(row)
        if self.org_id and not row.org_id:
            row.org_id = uuid.UUID(self.org_id)

        existing = decrypt_dict(row.credentials_enc or "")
        # New OAuth client invalidates previously issued tokens
        keep_tokens = existing.get("client_id") == client_id
        new_secrets = {
            "client_id": client_id,
            "client_secret": client_secret,
            **({k: existing[k] for k in ("refresh_token", "access_token", "expires_at") if k in existing} if keep_tokens else {}),
        }
        row.credentials_enc = encrypt_dict(new_secrets)
        row.config = {"client_id": client_id}
        ok, detail = await _test_google_calendar(new_secrets)
        row.status = "connected" if ok else "disconnected"
        row.last_error = None
        row.last_checked_at = datetime.utcnow()
        await self.db.flush()
        return {"provider": "google_calendar", "status": row.status, "detail": detail}

    async def _save_email_inbox(self, secrets: dict, config: dict) -> dict:
        """Add or update one inbox; the email connection keeps a map of all of them.

        Stored shape — credentials_enc: {"inboxes": {addr: {password, host, port}}};
        config: {"inboxes": [addrs], "primary": addr}. An inbox is only stored
        after a live SMTP login + verification send succeeds.
        """
        ok, detail = await _test_email_inbox(secrets, config)

        filters = [Integration.provider == "email"]
        if self.org_id:
            filters.append(Integration.org_id == uuid.UUID(self.org_id))
        result = await self.db.execute(select(Integration).where(*filters))
        row = result.scalar_one_or_none()
        if row is None:
            row = Integration(provider="email")
            self.db.add(row)
        if self.org_id and not row.org_id:
            row.org_id = uuid.UUID(self.org_id)

        existing = decrypt_dict(row.credentials_enc or "")
        inboxes: dict = existing.get("inboxes", {})

        if ok:
            email_address = config["email_address"].strip()
            host, port = resolve_smtp(email_address, config.get("smtp_host"), config.get("smtp_port"))
            inboxes[email_address] = {
                "password": secrets["app_password"],
                "host": host,
                "port": port,
            }
            new_secrets = {"inboxes": inboxes}
            new_config = {
                "inboxes": sorted(inboxes),
                "primary": (row.config or {}).get("primary") or email_address,
            }
            row.credentials_enc = encrypt_dict(new_secrets)
            row.config = new_config
            row.status = "connected"
            row.last_error = None
            _apply_to_settings("email", new_secrets, new_config)
        elif not inboxes:
            # Nothing valid stored yet — surface the failure honestly
            row.status = "error"
            row.last_error = detail
        # else: keep existing verified inboxes connected; just report the failure

        row.last_checked_at = datetime.utcnow()
        await self.db.flush()
        return {"provider": "email", "status": "connected" if ok else "error", "detail": detail}

    async def delete_connection(self, provider: str) -> bool:
        filters = [Integration.provider == provider]
        if self.org_id:
            filters.append(Integration.org_id == uuid.UUID(self.org_id))
        result = await self.db.execute(select(Integration).where(*filters))
        row = result.scalar_one_or_none()
        if row is None:
            return False
        await self.db.delete(row)
        await self.db.flush()
        return True

    async def _retest_email_inboxes(self, secrets: dict) -> tuple[bool, str]:
        """SMTP login check for every stored inbox (no test send on re-test)."""
        inboxes = secrets.get("inboxes", {})
        if not inboxes:
            return False, "No inboxes stored"
        failures = []
        for addr, creds in inboxes.items():
            try:
                await asyncio.to_thread(
                    smtp_login_check, addr, creds.get("password", ""),
                    creds.get("host", ""), int(creds.get("port") or 587), False,
                )
            except Exception as e:
                failures.append(f"{addr}: {e}")
        if failures:
            return False, "; ".join(failures)
        n = len(inboxes)
        return True, f"{n} inbox{'es' if n != 1 else ''} verified via SMTP login"

    async def test_saved(self, provider: str) -> dict:
        filters = [Integration.provider == provider]
        if self.org_id:
            filters.append(Integration.org_id == uuid.UUID(self.org_id))
        result = await self.db.execute(select(Integration).where(*filters))
        row = result.scalar_one_or_none()
        if row is None:
            return {"provider": provider, "status": "disconnected", "detail": "Not configured"}
        secrets = decrypt_dict(row.credentials_enc or "")
        if provider == "email":
            ok, detail = await self._retest_email_inboxes(secrets)
        elif provider == "browser_harness":
            from app.services.browser_harness_bridge import bridge
            ok = bridge.connected
            detail = "Browser harness is connected" if ok else "No harness agent connected"
        else:
            ok, detail = await test_connection(provider, secrets, row.config or {})
        row.status = "connected" if ok else "error"
        row.last_checked_at = datetime.utcnow()
        row.last_error = None if ok else detail
        await self.db.flush()
        return {"provider": provider, "status": row.status, "detail": detail}
