from fastapi import APIRouter, Depends
from app.core.errors import AppError
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
async def health_check():
    return {"status": "healthy", "service": "socrates-api"}


@router.get("/health/detailed")
async def detailed_health():
    checks = {
        "api": {"status": "ok"},
        "database": {"status": "unknown"},
        "redis": {"status": "unknown"},
    }
    # Try db
    try:
        from app.database.session import engine
        from sqlalchemy import text
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"]["status"] = "ok"
    except Exception as e:
        checks["database"]["status"] = "error"
        checks["database"]["error"] = str(e)

    # Try redis
    try:
        import redis.asyncio as aioredis
        from app.config import settings
        r = aioredis.from_url(settings.redis_url)
        await r.ping()
        checks["redis"]["status"] = "ok"
        await r.aclose()
    except Exception as e:
        checks["redis"]["status"] = "error"
        checks["redis"]["error"] = str(e)

    return {"status": "healthy", "service": "socrates-api", "checks": checks}


@router.get("/health/readiness")
async def readiness():
    return {"status": "ready"}


@router.get("/health/liveness")
async def liveness():
    return {"status": "alive"}
