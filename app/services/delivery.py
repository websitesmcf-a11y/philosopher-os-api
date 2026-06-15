"""Outbound delivery — one entry point for actually reaching a lead.

Resolves the recipient from the Lead record (phone for WhatsApp, email for
SMTP), delivers through the connected integration, and persists the
Conversation + Message rows so the timeline stays truthful. Used by the
Odysseus agent and the drip scheduler.
"""
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings
from app.database.models import Conversation, Lead, Message

logger = logging.getLogger(__name__)


async def deliver_to_lead(db, org_id, lead: Lead, channel: str, body: str, subject: str | None = None) -> dict:
    """Send `body` to a lead over `channel` and log it. Returns delivery status."""
    if channel == "whatsapp":
        if not lead.phone:
            return {"status": "skipped", "reason": f"Lead '{lead.name}' has no phone number for WhatsApp."}
        from app.integrations.whatsapp import whatsapp
        result = await whatsapp.send_message(lead.phone, body)
    elif channel == "email":
        if not lead.email:
            return {"status": "skipped", "reason": f"Lead '{lead.name}' has no email address."}
        if not (settings.smtp_user and settings.smtp_password and settings.smtp_host):
            return {"status": "not_connected", "reason": "No email inbox connected — connect one on the Connections page."}
        from app.integrations.smtp_email import smtp_send
        try:
            await asyncio.to_thread(
                smtp_send,
                host=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_user,
                password=settings.smtp_password,
                to=[lead.email],
                subject=subject or "Message from our team",
                text=body,
            )
            result = {"status": "sent", "to": lead.email, "channel": "email"}
        except Exception as e:
            result = {"status": "failed", "to": lead.email, "channel": "email", "error": str(e)}
    else:
        return {"status": "unsupported_channel", "reason": f"Channel '{channel}' is not deliverable per-lead."}

    delivered = result.get("status") == "sent"

    # Log the message against a conversation regardless of outcome (failed
    # sends are visible too — status carries the truth).
    try:
        conv_result = await db.execute(
            select(Conversation).where(
                Conversation.lead_id == lead.id,
                Conversation.channel == channel,
            )
        )
        conv = conv_result.scalar_one_or_none()
        if not conv:
            conv = Conversation(lead_id=lead.id, org_id=org_id, channel=channel)
            db.add(conv)
            await db.flush()
        msg = Message(
            conversation_id=conv.id,
            sender_type="agent",
            direction="out",
            body=body,
        )
        db.add(msg)
        conv.last_message_at = datetime.now(timezone.utc)
        if delivered:
            lead.last_contacted_at = datetime.now(timezone.utc)
            if not lead.first_contacted_at:
                lead.first_contacted_at = datetime.now(timezone.utc)
        await db.flush()
        result["conversation_id"] = str(conv.id)
        result["message_id"] = str(msg.id)
    except Exception as e:
        logger.warning(f"Could not log delivery for lead {lead.id}: {e}")

    return result
