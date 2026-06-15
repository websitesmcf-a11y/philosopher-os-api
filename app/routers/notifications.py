"""Notifications router — send test messages and list notification history."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.core.security import get_current_user, get_current_org
from app.database.models import Notification
from app.services.notification_service import send_notification

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/test")
async def send_test_notification(
    data: dict,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Send a test notification through the requested channel."""
    channel = data.get("channel", "whatsapp")

    if channel == "whatsapp":
        await send_notification(
            db=db,
            title="Test Notification",
            body="🧪 This is a test notification from Philosopher OS. If you received this, WhatsApp notifications are working correctly!",
            notification_type="test",
        )
        return {"status": "sent", "channel": "whatsapp"}

    elif channel == "email":
        # Check if SMTP is actually configured
        from app.config import settings as app_settings
        if not all([app_settings.smtp_host, app_settings.smtp_user, app_settings.smtp_password]):
            raise HTTPException(
                status_code=400,
                detail="SMTP not configured. Go to Integrations → Email to set up your email with an app password."
            )
        await send_notification(
            db=db,
            title="Test Notification — Philosopher OS",
            body="🧪 This is a test email from Philosopher OS.\n\nIf you received this, email notifications are working correctly!\n\nYou can manage your notification settings in the app.",
            notification_type="test",
        )
        return {"status": "sent", "channel": "email"}

    raise HTTPException(status_code=400, detail=f"Unknown channel: {channel}")


@router.get("/history")
async def get_notification_history(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Get recent notification history."""
    result = await db.execute(
        select(Notification)
        .where(Notification.org_id == org_id, Notification.user_id == user.get("id"))
        .order_by(desc(Notification.created_at))
        .limit(limit)
    )
    notifications = result.scalars().all()
    return {
        "items": [
            {
                "id": str(n.id),
                "type": n.type,
                "title": n.title,
                "body": n.body,
                "read": n.read,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in notifications
        ],
        "total": len(notifications),
    }
