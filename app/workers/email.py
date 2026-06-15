"""Email workers — transactional and campaign email via the connected SMTP inbox."""
import logging
from app.workers.celery_app import celery_app
from app.config import settings

logger = logging.getLogger(__name__)


def _send_smtp(to: str, subject: str, body: str, html: str = None):
    """Send an email via the primary connected SMTP inbox."""
    if not (settings.smtp_user and settings.smtp_password and settings.smtp_host):
        logger.warning("No SMTP inbox connected — email not sent")
        return False

    from app.integrations.smtp_email import smtp_send
    smtp_send(
        host=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user,
        password=settings.smtp_password,
        to=[to],
        subject=subject,
        text=body,
        html=html or body.replace("\n", "<br>"),
    )
    return True


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def send_transactional_email(self, template: str, to: str, data: dict):
    """Send a transactional email (welcome, invoice, notification, etc.)."""
    logger.info(f"Sending email '{template}' to {to}")
    try:
        subject_map = {
            "welcome": "Welcome to Socrates AI!",
            "invoice": f"Invoice {data.get('invoice_number', '')} from Socrates AI",
            "notification": data.get("subject", "Notification from Socrates AI"),
        }
        subject = subject_map.get(template, "Message from Socrates AI")

        body_map = {
            "welcome": f"Hi {data.get('name', 'there')},\n\nWelcome to Socrates AI! We're excited to have you on board.",
            "invoice": f"Hi {data.get('name', 'there')},\n\nYour invoice for ${data.get('amount', 0):,.2f} is ready.",
            "notification": data.get("body", "You have a new notification."),
        }
        body = body_map.get(template, data.get("body", ""))

        _send_smtp(to, subject, body)
        logger.info(f"Email '{template}' sent to {to}")
        return {"status": "sent", "to": to, "template": template}
    except Exception as exc:
        logger.error(f"Email to {to} failed: {exc}")
        try:
            self.retry(exc=exc)
        except Exception:
            return {"status": "failed", "to": to, "template": template, "error": str(exc)}


@celery_app.task(bind=True, max_retries=3, default_retry_delay=120)
def send_campaign_batch(self, campaign_id: str, batch: list):
    """Send a batch of campaign emails."""
    logger.info(f"Sending campaign batch for {campaign_id} ({len(batch)} recipients)")
    success_count = 0
    for recipient in batch:
        try:
            _send_smtp(
                to=recipient.get("email", ""),
                subject=recipient.get("subject", "Message from Socrates AI"),
                body=recipient.get("body", ""),
            )
            success_count += 1
        except Exception as e:
            logger.warning(f"Failed to send to {recipient.get('email', 'unknown')}: {e}")

    return {"status": "sent", "campaign_id": campaign_id, "count": success_count, "total": len(batch)}


@celery_app.task(bind=True, max_retries=2)
def handle_bounce(self, notification: dict):
    """Handle email bounce notifications."""
    email = notification.get("email", "unknown")
    logger.info(f"Handling bounce notification: {email}")
    try:
        engine = None
        if settings.supabase_db_url:
            from sqlalchemy import create_engine, text
            engine = create_engine(settings.supabase_db_url)
            with engine.connect() as conn:
                conn.execute(
                    text("UPDATE leads SET status = 'bounced' WHERE email = :email"),
                    {"email": email},
                )
                conn.commit()

        return {"status": "handled", "email": email}
    except Exception as exc:
        logger.error(f"Bounce handling failed: {exc}")
        return {"status": "failed", "email": email, "error": str(exc)}
