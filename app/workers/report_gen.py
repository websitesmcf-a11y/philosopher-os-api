"""Report generation workers — client reports, summaries, campaign reports."""
import logging
from datetime import datetime, date, timezone, timedelta
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _get_connection():
    from sqlalchemy import create_engine
    from app.config import settings
    engine = create_engine(settings.supabase_db_url) if settings.supabase_db_url else None
    return engine


@celery_app.task(bind=True, max_retries=2)
def generate_client_report(self, client_id: str):
    """Generate a performance report for a specific client."""
    logger.info(f"Generating report for client {client_id}")
    try:
        engine = _get_connection()
        if not engine:
            return {"status": "skipped", "client_id": client_id}

        from sqlalchemy import text

        with engine.connect() as conn:
            client = conn.execute(
                text("SELECT name, mrr, lifetime_value, contract_status FROM clients WHERE id = :cid"),
                {"cid": client_id},
            ).fetchone()

            if not client:
                return {"status": "not_found", "client_id": client_id}

            invoices = conn.execute(
                text("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM invoices WHERE client_id = :cid AND status = 'paid'"),
                {"cid": client_id},
            ).fetchone()

            recent_msgs = conn.execute(
                text("SELECT COUNT(*) FROM messages WHERE created_at >= :start AND conversation_id IN (SELECT id FROM conversations WHERE client_id = :cid)"),
                {"start": (date.today() - timedelta(days=30)).isoformat(), "cid": client_id},
            ).scalar() or 0

        report = (
            f"Client Report: {client[0]}\n"
            f"MRR: ${client[1]:,.2f}\n"
            f"Lifetime Value: ${client[2]:,.2f}\n"
            f"Status: {client[3]}\n"
            f"Paid Invoices: {invoices[0] if invoices else 0} (${invoices[1]:,.2f} total)\n"
            f"Messages (30d): {recent_msgs}\n"
        )

        return {"status": "generated", "client_id": client_id, "report": report}
    except Exception as exc:
        logger.error(f"Client report generation failed: {exc}")
        try:
            self.retry(exc=exc)
        except Exception:
            return {"status": "failed", "client_id": client_id, "error": str(exc)}


@celery_app.task(bind=True, max_retries=2)
def generate_monthly_summary(self, org_id: str):
    """Generate monthly business summary for the agency."""
    logger.info(f"Generating monthly summary for org {org_id}")
    try:
        engine = _get_connection()
        if not engine:
            return {"status": "skipped", "org_id": org_id}

        from sqlalchemy import text

        month_start = date.today().replace(day=1).isoformat()
        prev_month_start = (date.today().replace(day=1) - timedelta(days=1)).replace(day=1).isoformat()

        with engine.connect() as conn:
            leads = conn.execute(
                text("SELECT COUNT(*) FROM leads WHERE org_id = :oid AND created_at >= :start"),
                {"oid": org_id, "start": month_start},
            ).scalar() or 0

            prev_leads = conn.execute(
                text("SELECT COUNT(*) FROM leads WHERE org_id = :oid AND created_at >= :start AND created_at < :end"),
                {"oid": org_id, "start": prev_month_start, "end": month_start},
            ).scalar() or 0

            revenue = conn.execute(
                text("SELECT COALESCE(SUM(amount), 0) FROM revenue_events WHERE org_id = :oid AND created_at >= :start"),
                {"oid": org_id, "start": month_start},
            ).scalar() or 0.0

            mrr = conn.execute(
                text("SELECT COALESCE(SUM(mrr), 0) FROM clients WHERE org_id = :oid AND contract_status = 'active'"),
                {"oid": org_id},
            ).scalar() or 0.0

        summary = (
            f"Monthly Summary\n"
            f"New Leads This Month: {leads} (prev: {prev_leads})\n"
            f"Revenue This Month: ${float(revenue):,.2f}\n"
            f"Current MRR: ${float(mrr):,.2f}\n"
        )

        return {"status": "generated", "org_id": org_id, "summary": summary}
    except Exception as exc:
        logger.error(f"Monthly summary generation failed: {exc}")
        return {"status": "failed", "org_id": org_id, "error": str(exc)}


@celery_app.task(bind=True, max_retries=2)
def generate_campaign_report(self, campaign_id: str):
    """Generate a post-campaign performance report."""
    logger.info(f"Generating campaign report for {campaign_id}")
    try:
        engine = _get_connection()
        if not engine:
            return {"status": "skipped", "campaign_id": campaign_id}

        from sqlalchemy import text

        with engine.connect() as conn:
            campaign = conn.execute(
                text("SELECT name, target_count, sent_count, reply_count, conversion_count, channel, status FROM campaigns WHERE id = :cid"),
                {"cid": campaign_id},
            ).fetchone()

            if not campaign:
                return {"status": "not_found", "campaign_id": campaign_id}

        rate = (campaign[3] / campaign[2] * 100) if campaign[2] else 0
        conv_rate = (campaign[4] / campaign[2] * 100) if campaign[2] else 0

        report = (
            f"Campaign Report: {campaign[0]}\n"
            f"Channel: {campaign[5]} | Status: {campaign[6]}\n"
            f"Target: {campaign[1]} | Sent: {campaign[2]}\n"
            f"Replies: {campaign[3]} ({rate:.1f}% reply rate)\n"
            f"Conversions: {campaign[4]} ({conv_rate:.1f}% conversion rate)\n"
        )

        return {"status": "generated", "campaign_id": campaign_id, "report": report}
    except Exception as exc:
        logger.error(f"Campaign report generation failed: {exc}")
        return {"status": "failed", "campaign_id": campaign_id, "error": str(exc)}
