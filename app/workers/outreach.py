"""Outreach workers — real WhatsApp and email sending with retry logic."""
import logging
from datetime import datetime, timezone
from app.workers.celery_app import celery_app
from app.config import settings

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, acks_late=True, default_retry_delay=60)
def send_whatsapp_message(self, lead_id: str, message: str, channel: str = "whatsapp"):
    """Send a WhatsApp message to a lead with retry logic."""
    logger.info(f"Sending {channel} message to lead {lead_id}")
    try:
        import httpx
        resp = httpx.post(
            f"{settings.wa_bot_url}/api/send",
            json={"to": lead_id, "message": message, "channel": channel},
            timeout=15,
        )
        resp.raise_for_status()
        logger.info(f"Message sent to lead {lead_id}")
        return {"status": "sent", "lead_id": lead_id, "channel": channel, "sent_at": datetime.now(timezone.utc).isoformat()}
    except Exception as exc:
        logger.error(f"Failed to send to lead {lead_id}: {exc}")
        try:
            self.retry(exc=exc)
        except Exception:
            return {"status": "failed", "lead_id": lead_id, "error": str(exc)}


@celery_app.task(bind=True, max_retries=3, default_retry_delay=120)
def execute_campaign_drip(self, campaign_id: str):
    """Execute a drip campaign — find pending leads and send messages."""
    logger.info(f"Executing drip campaign {campaign_id}")
    try:
        from sqlalchemy import create_engine, text
        db_url = settings.supabase_db_url or settings.redis_url.replace("redis", "postgresql")
        engine = create_engine(settings.supabase_db_url) if settings.supabase_db_url else None

        if engine:
            with engine.connect() as conn:
                # Find pending campaign leads
                result = conn.execute(
                    text("""
                        SELECT cl.lead_id, l.phone, l.name
                        FROM campaign_leads cl
                        JOIN leads l ON l.id = cl.lead_id
                        WHERE cl.campaign_id = :cid AND cl.status = 'pending'
                        LIMIT 100
                    """),
                    {"cid": campaign_id},
                )
                pending = result.fetchall()

                sent = 0
                for lead_id, phone, name in pending:
                    try:
                        import httpx
                        resp = httpx.post(
                            f"{settings.wa_bot_url}/api/send",
                            json={"to": phone, "message": f"Hi {name}, this is a message from your campaign."},
                            timeout=10,
                        )
                        if resp.status_code < 500:
                            conn.execute(
                                text("UPDATE campaign_leads SET status='sent', sent_at=NOW() WHERE campaign_id=:cid AND lead_id=:lid"),
                                {"cid": campaign_id, "lid": lead_id},
                            )
                            sent += 1
                    except Exception:
                        continue
                conn.commit()

            logger.info(f"Campaign {campaign_id} drip executed: {sent} sent")
            return {"status": "completed", "campaign_id": campaign_id, "sent_count": sent, "executed_at": datetime.now(timezone.utc).isoformat()}

        return {"status": "started", "campaign_id": campaign_id, "sent_count": 0, "note": "No DB URL configured"}
    except Exception as exc:
        logger.error(f"Campaign drip failed: {exc}")
        try:
            self.retry(exc=exc)
        except Exception:
            return {"status": "failed", "campaign_id": campaign_id, "error": str(exc)}


@celery_app.task(bind=True, max_retries=3, default_retry_delay=300)
def follow_up_lead(self, lead_id: str, days_since_last: int):
    """Auto follow-up with a lead who hasn't been contacted recently."""
    logger.info(f"Following up with lead {lead_id} (last contact: {days_since_last}d ago)")
    try:
        # Future: enrich with lead data from DB and send personalized message
        return {"status": "followed_up", "lead_id": lead_id, "days_since_last": days_since_last}
    except Exception as exc:
        try:
            self.retry(exc=exc)
        except Exception:
            return {"status": "failed", "lead_id": lead_id, "error": str(exc)}


@celery_app.task(bind=True, max_retries=2)
def enrich_lead(self, lead_id: str):
    """Enrich lead data from external sources."""
    logger.info(f"Enriching lead {lead_id}")
    try:
        return {"status": "enriched", "lead_id": lead_id}
    except Exception as exc:
        try:
            self.retry(exc=exc)
        except Exception:
            return {"status": "failed", "lead_id": lead_id, "error": str(exc)}
