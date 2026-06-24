"""WhatsApp integration — outbound messaging via wa_bot at configurable URL.

Supports multi-session wa-bot: pass ?session=X or { session } body field
to route messages to a specific WhatsApp account.
"""

import logging
import httpx
from typing import Any
from app.config import settings

logger = logging.getLogger(__name__)


class WhatsAppClient:
    """Client for WhatsApp messaging via the wa_bot HTTP service."""

    def __init__(self):
        self.base_url = settings.wa_bot_url.rstrip("/")

    async def send_message(self, to: str, message: str, session: str = "") -> dict:
        """Send a WhatsApp message via wa_bot /api/send.

        Args:
            to: Phone number (digits only).
            message: Message text.
            session: Session ID for multi-session wa-bot (optional).
        """
        payload = {"to": to, "message": message}
        if session:
            payload["session"] = session
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{self.base_url}/api/send",
                    json=payload,
                )
                data = resp.json() if resp.content else {}
                wa_status = data.get("status") or data.get("result") or ""
                error_msg = data.get("error") or data.get("message") or ""
                actually_sent = resp.status_code < 300 and wa_status in ("sent", "success", "ok", "queued", "")
                if actually_sent:
                    logger.info(f"WhatsApp sent to {to}: {wa_status or 'ok'}")
                    return {"status": "sent", "to": to, "channel": "whatsapp", "session": session, "response": data}
                else:
                    logger.warning(f"WhatsApp send to {to} failed (HTTP {resp.status_code}): {error_msg or wa_status}")
                    return {"status": "failed", "to": to, "channel": "whatsapp", "session": session,
                            "error": error_msg or wa_status or f"HTTP {resp.status_code}", "response": data}
        except httpx.RequestError as e:
            logger.warning(f"WhatsApp send_message failed (wa_bot unreachable at {self.base_url}): {e}")
            return {"status": "not_connected", "to": to, "channel": "whatsapp", "error": str(e),
                    "reason": f"WhatsApp bridge is unreachable at {self.base_url}. Is it running?"}
        except Exception as e:
            logger.error(f"WhatsApp send_message error: {e}")
            return {"status": "failed", "to": to, "channel": "whatsapp", "session": session, "error": str(e)}

    async def send_template(self, to: str, template_name: str, variables: dict, session: str = "") -> dict:
        """Send a WhatsApp template message via wa_bot."""
        payload = {"to": to, "template": template_name, "variables": variables}
        if session:
            payload["session"] = session
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{self.base_url}/api/send-template",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                logger.info(f"WhatsApp template '{template_name}' sent to {to} (session={session or 'default'})")
                return {"status": "sent", "to": to, "template": template_name, "session": session, "response": data}
        except httpx.RequestError as e:
            logger.warning(f"WhatsApp send_template failed (wa_bot unreachable): {e}")
            return {"status": "failed", "to": to, "template": template_name, "session": session, "error": str(e)}
        except Exception as e:
            logger.error(f"WhatsApp send_template error: {e}")
            return {"status": "failed", "to": to, "template": template_name, "session": session, "error": str(e)}

    async def get_whatsapp_status(self, session: str = "") -> dict:
        """Check wa_bot connection and WhatsApp auth status.

        Args:
            session: Session ID (empty = aggregate/all sessions).
        """
        try:
            params = {}
            if session:
                params["session"] = session
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.base_url}/api/status", params=params)
                resp.raise_for_status()
                data = resp.json()
                # Single-session response
                if "session" in data:
                    return {
                        "status": data.get("status", "connected"),
                        "connected": data.get("connected", True),
                        "qr_code": data.get("qr_url"),
                        "phone": data.get("phone"),
                        "session": data.get("session"),
                    }
                # Aggregate response (list of sessions)
                return {
                    "status": data.get("sessions", [{}])[0].get("status", "connected") if data.get("sessions") else "unknown",
                    "connected": any(s.get("connected") for s in data.get("sessions", [])),
                    "sessions": data.get("sessions", []),
                    "total": data.get("total", 0),
                    "connected_count": data.get("connected", 0),
                }
        except httpx.RequestError as e:
            logger.warning(f"WhatsApp status check failed (wa_bot unreachable): {e}")
            return {"status": "unknown", "connected": False, "error": str(e)}
        except Exception as e:
            logger.error(f"WhatsApp get_status error: {e}")
            return {"status": "error", "connected": False, "error": str(e)}

    async def create_session(self, session_id: str) -> dict:
        """Create a new WhatsApp session on the multi-session wa-bot."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self.base_url}/api/sessions",
                    json={"id": session_id},
                )
                data = resp.json()
                return {
                    "session": data.get("session", session_id),
                    "status": data.get("status", "created"),
                    "qr_available": data.get("qr_available", False),
                    "qr_url": data.get("qr_url"),
                }
        except httpx.RequestError as e:
            logger.warning(f"Create session failed (wa_bot unreachable): {e}")
            return {"session": session_id, "status": "error", "error": str(e)}

    async def list_sessions(self) -> dict:
        """List all sessions on the wa-bot."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.base_url}/api/sessions")
                return resp.json()
        except httpx.RequestError as e:
            return {"sessions": [], "total": 0, "connected": 0, "error": str(e)}


whatsapp = WhatsAppClient()
