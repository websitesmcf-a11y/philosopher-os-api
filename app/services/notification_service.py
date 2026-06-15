"""Notification service — delivers alerts via all connected channels.

When something important happens (Beast Mode starts/ends, campaign launches,
calendar event fires), this service creates a Notification row in the database
AND sends it through every available channel (WhatsApp, email, desktop push).
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import Notification, Integration

logger = logging.getLogger(__name__)


async def _get_whatsapp_phone(db: AsyncSession) -> str | None:
    """Get the user's WhatsApp notification number from settings."""
    result = await db.execute(
        select(Integration).where(Integration.provider == "whatsapp")
    )
    row = result.scalar_one_or_none()
    if row and row.config:
        return row.config.get("phone") or None
    return None


async def _send_whatsapp_message(phone: str, message: str) -> bool:
    """Send a WhatsApp message via wa-bot."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "http://localhost:8088/api/send",
                json={"to": phone, "message": message},
            )
            return resp.status_code == 200
    except Exception as e:
        logger.warning(f"WhatsApp send failed: {e}")
        return False


async def _send_email(to: str, subject: str, body: str) -> bool:
    """Send an email via the configured SMTP."""
    import smtplib
    from email.mime.text import MIMEText

    host = settings.smtp_host
    port = settings.smtp_port
    user = settings.smtp_user
    password = settings.smtp_password
    if not all([host, port, user, password]):
        logger.warning("Email not sent — SMTP not configured")
        return False

    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = to

        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(msg)
        return True
    except Exception as e:
        logger.warning(f"Email send failed: {e}")
        return False


async def _get_notification_recipients(db: AsyncSession) -> dict:
    """Get user contact info for all notification channels.

    WhatsApp: checks wa-bot directly (real connection status, not a DB flag).
    Email: checks SMTP settings (must have host + user + password).
    """
    channels: dict[str, list[str]] = {"whatsapp": [], "email": []}

    # WhatsApp — check wa-bot directly
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            wa_resp = await client.get("http://localhost:8088/api/status")
            if wa_resp.status_code == 200:
                wa_data = wa_resp.json()
                if wa_data.get("connected") and wa_data.get("phone"):
                    channels["whatsapp"].append(wa_data["phone"])
    except Exception:
        logger.warning("WhatsApp check failed — wa-bot unreachable")

    # Email — check actual SMTP config is populated
    if settings.smtp_host and settings.smtp_user and settings.smtp_password:
        channels["email"].append(settings.smtp_user)

    return channels


async def send_notification(
    db: AsyncSession,
    title: str,
    body: str,
    notification_type: str = "system",
    org_id: str = "00000000-0000-0000-0000-000000000001",
    user_id: str = "00000000-0000-0000-0000-000000000010",
) -> None:
    """Send a notification through ALL connected channels.

    Creates a DB row + sends via WhatsApp + sends via email.
    """
    # 1. Store in database
    notif = Notification(
        org_id=org_id,
        user_id=user_id,
        type=notification_type,
        title=title,
        body=body,
        data={},
        read=False,
    )
    db.add(notif)
    await db.flush()

    # 2. Get recipients
    recipients = await _get_notification_recipients(db)

    # 3. Send via WhatsApp
    whatsapp_phones = recipients.get("whatsapp", [])
    for phone in whatsapp_phones:
        wa_msg = f"🔔 *{title}*\n\n{body}"
        sent = await _send_whatsapp_message(phone, wa_msg)
        if sent:
            logger.info(f"WhatsApp notification sent to {phone}: {title}")
        else:
            logger.warning(f"WhatsApp notification failed for {phone}")

    # 4. Send via Email
    email_addrs = recipients.get("email", [])
    smtp_configured = bool(settings.smtp_host and settings.smtp_user and settings.smtp_password)
    if email_addrs and smtp_configured:
        for addr in email_addrs:
            sent = await _send_email(addr, f"🔔 Philosopher OS — {title}", body)
            if sent:
                logger.info(f"Email notification sent to {addr}: {title}")
            else:
                logger.warning(f"Email notification failed for {addr}")
    elif email_addrs and not smtp_configured:
        logger.warning(f"Email notifications enabled but SMTP not configured — skipped")

    await db.commit()


async def notify_beast_mode_event(
    db: AsyncSession,
    event: str,  # "started" | "completed" | "failed"
    objective: str,
    level: str = "",
    mission_id: str = "",
) -> None:
    """Notify when a Beast Mode mission starts, completes, or fails."""
    emoji = {"started": "⚡", "completed": "✅", "failed": "❌"}.get(event, "🔔")
    title = f"Beast Mode {event.title()}"
    body = (
        f"{emoji} Beast Mode mission {event}!\n\n"
        f"Objective: {objective}\n"
        f"Level: {level}\n"
        f"Time: {datetime.now(timezone.utc).strftime('%H:%M')}"
    )
    await send_notification(db, title, body, notification_type="beast_mode")


async def notify_campaign_event(
    db: AsyncSession,
    event: str,  # "created" | "launched" | "completed" | "paused"
    campaign_name: str,
    channel: str = "",
    lead_count: int = 0,
) -> None:
    """Notify when a campaign is created, launched, completes, or pauses."""
    emoji = {"created": "🆕", "launched": "🚀", "completed": "✅", "paused": "⏸️"}.get(event, "🔔")
    title = f"Campaign {event.title()}"
    body = (
        f"{emoji} Campaign '{campaign_name}' {event}!\n\n"
        f"Channel: {channel}\n"
        f"Target: {lead_count} leads\n"
        f"Time: {datetime.now(timezone.utc).strftime('%H:%M')}"
    )
    await send_notification(db, title, body, notification_type="campaign")


async def notify_calendar_event(
    db: AsyncSession,
    event: str,  # "starting" | "ending"
    event_title: str,
    event_type: str = "",
    start_time: str = "",
) -> None:
    """Notify when a calendar event is starting or ending."""
    emoji = {"starting": "📅", "ending": "✅"}.get(event, "🔔")
    title = f"Calendar Event {event.title()}"
    body = (
        f"{emoji} '{event_title}' is {event} now!\n\n"
        f"Type: {event_type}\n"
        f"Scheduled: {start_time}"
    )
    await send_notification(db, title, body, notification_type="calendar")
