"""WhatsApp integration — outbound messaging via wa_bot at configurable URL."""

import logging
import httpx
from typing import Any
from app.config import settings

logger = logging.getLogger(__name__)


class WhatsAppClient:
    """Client for WhatsApp messaging via the wa_bot HTTP service."""

    def __init__(self):
        self.base_url = settings.wa_bot_url.rstrip("/")

    async def send_message(self, to: str, message: str) -> dict:
        """Send a WhatsApp message via wa_bot /api/send."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{self.base_url}/api/send",
                    json={"to": to, "message": message},
                )
                resp.raise_for_status()
                data = resp.json()
                logger.info(f"WhatsApp message sent to {to}: {data.get('status', 'ok')}")
                return {"status": "sent", "to": to, "channel": "whatsapp", "response": data}
        except httpx.RequestError as e:
            logger.warning(f"WhatsApp send_message failed (wa_bot unreachable at {self.base_url}): {e}")
            return {"status": "failed", "to": to, "channel": "whatsapp", "error": str(e)}
        except Exception as e:
            logger.error(f"WhatsApp send_message error: {e}")
            return {"status": "failed", "to": to, "channel": "whatsapp", "error": str(e)}

    async def send_template(self, to: str, template_name: str, variables: dict) -> dict:
        """Send a WhatsApp template message via wa_bot."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{self.base_url}/api/send-template",
                    json={"to": to, "template": template_name, "variables": variables},
                )
                resp.raise_for_status()
                data = resp.json()
                logger.info(f"WhatsApp template '{template_name}' sent to {to}")
                return {"status": "sent", "to": to, "template": template_name, "response": data}
        except httpx.RequestError as e:
            logger.warning(f"WhatsApp send_template failed (wa_bot unreachable): {e}")
            return {"status": "failed", "to": to, "template": template_name, "error": str(e)}
        except Exception as e:
            logger.error(f"WhatsApp send_template error: {e}")
            return {"status": "failed", "to": to, "template": template_name, "error": str(e)}

    async def get_whatsapp_status(self) -> dict:
        """Check wa_bot connection and WhatsApp auth status."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.base_url}/api/status")
                resp.raise_for_status()
                data = resp.json()
                return {
                    "status": data.get("status", "connected"),
                    "connected": data.get("connected", True),
                    "qr_code": data.get("qr_code"),
                    "phone": data.get("phone"),
                }
        except httpx.RequestError as e:
            logger.warning(f"WhatsApp status check failed (wa_bot unreachable): {e}")
            return {"status": "unknown", "connected": False, "qr_code": None, "error": str(e)}
        except Exception as e:
            logger.error(f"WhatsApp get_status error: {e}")
            return {"status": "error", "connected": False, "qr_code": None, "error": str(e)}


whatsapp = WhatsAppClient()
