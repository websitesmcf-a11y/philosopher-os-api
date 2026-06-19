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


@router.get("/health/llm")
async def llm_health():
    """Diagnostic: shows every provider's configuration and rate-limit state."""
    import time
    from app.config import settings
    from app.llm.client import llm
    from app.llm.openrouter_client import WATERFALL

    providers = {}

    # OpenRouter
    or_key = settings.openrouter_api_key
    or_prov = llm._providers.get("openrouter")
    if or_prov:
        now = time.time()
        rl = {m: round(exp - now, 1) for m, exp in or_prov._rate_limits.items() if exp > now}
        available = [m for m in WATERFALL if or_prov._rate_limits.get(m, 0) <= now]
        providers["openrouter"] = {
            "key_configured": bool(or_key),
            "key_prefix": or_key[:12] + "..." if or_key else None,
            "waterfall_total": len(WATERFALL),
            "available_models": len(available),
            "blocked_models": rl,
            "current_model": getattr(or_prov, "_last_used_model", WATERFALL[0]),
        }
    else:
        providers["openrouter"] = {
            "key_configured": bool(or_key),
            "error": "NOT in providers — key missing or _build_providers skipped it",
        }

    # Ollama
    ollama_url = settings.ollama_url or "http://localhost:11434"
    providers["ollama"] = {"url": ollama_url}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(f"{ollama_url}/api/tags")
            providers["ollama"]["reachable"] = r.status_code == 200
            if r.status_code == 200:
                models = [m["name"] for m in r.json().get("models", [])]
                providers["ollama"]["models"] = models
    except Exception as e:
        providers["ollama"]["reachable"] = False
        providers["ollama"]["error"] = str(e)

    # Other cloud providers
    for prov_name, key in [
        ("anthropic", settings.anthropic_api_key),
        ("openai", settings.openai_api_key),
        ("deepseek", settings.deepseek_api_key),
    ]:
        providers[prov_name] = {"key_configured": bool(key)}

    configured = [p for p, info in providers.items() if info.get("key_configured") or info.get("reachable")]
    return {
        "configured_providers": configured,
        "active_provider": llm.active_provider,
        "providers": providers,
    }
