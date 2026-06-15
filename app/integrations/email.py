"""Email integration — outbound mail via the connected SMTP inbox.

The primary inbox (saved on the Connections page) is applied to settings at
startup; sends fail honestly with status "not_configured" when no inbox is
connected.
"""
import asyncio
import logging

from app.config import settings
from app.integrations.smtp_email import smtp_send

logger = logging.getLogger(__name__)


class EmailClient:
    """Send email through the connected SMTP inbox (app-password auth)."""

    @property
    def configured(self) -> bool:
        return bool(settings.smtp_user and settings.smtp_password and settings.smtp_host)

    async def send_email(
        self,
        to: str | list[str],
        subject: str,
        html: str | None = None,
        text: str | None = None,
        from_email: str | None = None,
        reply_to: str | None = None,
    ) -> dict:
        """Send an email via the primary connected inbox."""
        recipients = to if isinstance(to, list) else [to]
        if not self.configured:
            logger.warning("No SMTP inbox connected — email not sent")
            return {"status": "not_configured", "to": recipients, "subject": subject}

        try:
            await asyncio.to_thread(
                smtp_send,
                host=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_user,
                password=settings.smtp_password,
                to=recipients,
                subject=subject,
                html=html,
                text=text,
                from_email=from_email,
                reply_to=reply_to,
            )
            return {"status": "sent", "to": recipients, "subject": subject}
        except Exception as e:
            logger.error(f"Email send failed: {e}")
            return {"status": "failed", "error": str(e), "to": recipients}

    async def send_template(self, to: str, template: str, data: dict) -> dict:
        """Send a templated email."""
        return await self.send_email(
            to=to,
            subject=data.get("subject", "Update from Socrates AI"),
            html=data.get("html", f"<p>{data.get('message', '')}</p>"),
        )


email_client = EmailClient()
