"""Research workers — market intelligence, lead discovery, news monitoring."""
import logging
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=120)
def research_industry(self, industry_name: str):
    """Research an industry — competitors, trends, opportunities."""
    logger.info(f"Researching industry: {industry_name}")
    try:
        from app.config import settings

        if settings.browser_harness_url:
            import httpx
            resp = httpx.post(
                f"{settings.browser_harness_url}/api/search",
                json={"query": f"{industry_name} industry trends competitors 2026", "depth": "deep"},
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            return {"status": "complete", "industry": industry_name, "findings": data.get("results", [])}

        return {"status": "complete", "industry": industry_name, "findings": [], "note": "Browser Harness not configured"}
    except Exception as exc:
        logger.error(f"Industry research failed: {exc}")
        try:
            self.retry(exc=exc)
        except Exception:
            return {"status": "failed", "industry": industry_name, "error": str(exc)}


@celery_app.task(bind=True, max_retries=2, default_retry_delay=120)
def find_leads(self, industry: str, location: str):
    """Find lead sources for a target market."""
    logger.info(f"Finding leads in {industry} / {location}")
    try:
        from app.config import settings

        if settings.browser_harness_url:
            import httpx
            resp = httpx.post(
                f"{settings.browser_harness_url}/api/search",
                json={"query": f"{industry} companies in {location} business directory", "depth": "quick"},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            return {"status": "complete", "industry": industry, "location": location, "leads_found": len(data.get("results", []))}

        return {"status": "complete", "industry": industry, "location": location, "leads_found": 0, "note": "Browser Harness not configured"}
    except Exception as exc:
        logger.error(f"Lead finding failed: {exc}")
        try:
            self.retry(exc=exc)
        except Exception:
            return {"status": "failed", "industry": industry, "location": location, "error": str(exc)}


@celery_app.task(bind=True, max_retries=2)
def monitor_news(self, keywords: list):
    """Monitor news sources for specific keywords."""
    logger.info(f"Monitoring news for: {keywords}")
    try:
        from app.config import settings

        if settings.browser_harness_url:
            import httpx
            results = []
            for kw in keywords:
                try:
                    resp = httpx.post(
                        f"{settings.browser_harness_url}/api/search",
                        json={"query": kw, "depth": "quick"},
                        timeout=15,
                    )
                    if resp.status_code == 200:
                        results.extend(resp.json().get("results", []))
                except Exception:
                    continue

            return {"status": "monitoring", "keywords": keywords, "articles_found": len(results)}

        return {"status": "monitoring", "keywords": keywords, "note": "Browser Harness not configured"}
    except Exception as exc:
        logger.error(f"News monitoring failed: {exc}")
        return {"status": "failed", "keywords": keywords, "error": str(exc)}
