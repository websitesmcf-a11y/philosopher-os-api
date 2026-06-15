"""Analytics workers — compute metrics, refresh caches, generate reports."""
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
def compute_daily_metrics(self, org_id: str, date_str: str):
    """Compute and cache daily business metrics."""
    logger.info(f"Computing daily metrics for org {org_id} on {date_str}")
    try:
        engine = _get_connection()
        if not engine:
            return {"status": "skipped", "org_id": org_id, "note": "No DB URL"}

        from sqlalchemy import text

        with engine.connect() as conn:
            new_leads = conn.execute(
                text("SELECT COUNT(*) FROM leads WHERE org_id = :oid AND DATE(created_at) = :dt"),
                {"oid": org_id, "dt": date_str},
            ).scalar() or 0

            revenue = conn.execute(
                text("SELECT COALESCE(SUM(amount), 0) FROM revenue_events WHERE org_id = :oid AND DATE(created_at) = :dt"),
                {"oid": org_id, "dt": date_str},
            ).scalar() or 0.0

            messages_sent = conn.execute(
                text("SELECT COUNT(*) FROM messages WHERE DATE(created_at) = :dt"),
                {"dt": date_str},
            ).scalar() or 0

        return {
            "status": "computed",
            "org_id": org_id,
            "date": date_str,
            "new_leads": new_leads,
            "revenue": float(revenue),
            "messages_sent": messages_sent,
        }
    except Exception as exc:
        logger.error(f"Daily metrics computation failed: {exc}")
        try:
            self.retry(exc=exc)
        except Exception:
            return {"status": "failed", "org_id": org_id, "error": str(exc)}


@celery_app.task(bind=True, max_retries=2)
def compute_mrr(self, org_id: str):
    """Compute Monthly Recurring Revenue."""
    logger.info(f"Computing MRR for org {org_id}")
    try:
        engine = _get_connection()
        if not engine:
            return {"status": "skipped", "org_id": org_id}

        from sqlalchemy import text

        with engine.connect() as conn:
            total_mrr = conn.execute(
                text("SELECT COALESCE(SUM(mrr), 0) FROM clients WHERE org_id = :oid AND contract_status = 'active'"),
                {"oid": org_id},
            ).scalar() or 0.0

            new_business = conn.execute(
                text("""
                    SELECT COALESCE(SUM(mrr), 0) FROM clients
                    WHERE org_id = :oid AND contract_status = 'active'
                    AND created_at >= :start
                """),
                {"oid": org_id, "start": date.today().replace(day=1).isoformat()},
            ).scalar() or 0.0

        return {
            "status": "computed",
            "org_id": org_id,
            "total_mrr": float(total_mrr),
            "new_business": float(new_business),
            "period": "monthly",
        }
    except Exception as exc:
        logger.error(f"MRR computation failed: {exc}")
        return {"status": "failed", "org_id": org_id, "error": str(exc)}


@celery_app.task(bind=True, max_retries=2)
def refresh_dashboard_cache(self, org_id: str):
    """Refresh cached dashboard metrics."""
    logger.info(f"Refreshing dashboard cache for org {org_id}")
    try:
        import json
        import redis.asyncio as aioredis
        from app.config import settings

        r = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
        # Signal that cache should be refreshed on next read
        r.delete(f"dashboard:{org_id}")
        r.close()

        return {"status": "refreshed", "org_id": org_id}
    except Exception as exc:
        logger.warning(f"Dashboard cache refresh failed: {exc}")
        return {"status": "failed", "org_id": org_id, "error": str(exc)}


@celery_app.task(bind=True, max_retries=2)
def generate_weekly_report(self, org_id: str):
    """Generate and send weekly performance report."""
    logger.info(f"Generating weekly report for org {org_id}")
    try:
        engine = _get_connection()
        if not engine:
            return {"status": "skipped", "org_id": org_id}

        from sqlalchemy import text
        from datetime import date, timedelta

        week_ago = (date.today() - timedelta(days=7)).isoformat()

        with engine.connect() as conn:
            leads = conn.execute(
                text("SELECT COUNT(*) FROM leads WHERE org_id = :oid AND created_at >= :start"),
                {"oid": org_id, "start": week_ago},
            ).scalar() or 0

            messages = conn.execute(
                text("SELECT COUNT(*) FROM messages WHERE created_at >= :start"),
                {"start": week_ago},
            ).scalar() or 0

            revenue = conn.execute(
                text("SELECT COALESCE(SUM(amount), 0) FROM revenue_events WHERE org_id = :oid AND created_at >= :start"),
                {"oid": org_id, "start": week_ago},
            ).scalar() or 0.0

        return {
            "status": "generated",
            "org_id": org_id,
            "report": {
                "period": "weekly",
                "new_leads": leads,
                "messages_sent": messages,
                "revenue": float(revenue),
            },
        }
    except Exception as exc:
        logger.error(f"Weekly report generation failed: {exc}")
        return {"status": "failed", "org_id": org_id, "error": str(exc)}
