from fastapi import APIRouter
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


@router.get("/health/model")
async def model_status():
    """Returns the current active LLM provider and model."""
    from app.llm.client import llm
    from app.llm.openrouter_client import WATERFALL

    prov_name = llm.active_provider
    prov = llm._providers.get(prov_name)

    result: dict = {"provider": prov_name, "model": llm.active_model}

    if hasattr(prov, "rate_limited_models"):
        rl = prov.rate_limited_models
        result["rate_limited"] = rl
        result["waterfall_position"] = next(
            (i + 1 for i, m in enumerate(WATERFALL) if m == prov.current_model), None
        )
        result["waterfall_total"] = len(WATERFALL)
        result["using_local"] = False
    elif prov_name == "ollama":
        result["using_local"] = True
        result["rate_limited"] = []
    else:
        result["using_local"] = False
        result["rate_limited"] = []

    return result
