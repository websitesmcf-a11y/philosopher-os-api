from fastapi import FastAPI
from starlette.requests import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from contextlib import asynccontextmanager
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.core.logging import setup_logging, get_request_id_filter
from app.core.errors import setup_error_handlers
from app.core.rate_limit import RateLimitMiddleware
from prometheus_client import make_asgi_app
from app.monitoring.metrics import http_requests_total, http_request_duration, agent_actions_total
from app.routers import (
    auth, users, leads, clients, campaigns, messages,
    agents, finance, analytics, knowledge, calendar,
    tasks, automation, webhooks, chat, health, connections, cleanup,
    lead_lists, notifications,
)
from app.database.session import engine, init_db
from app.agents.council import CouncilOrchestrator
from app.agents.plato import Plato, set_hermes as plato_set_hermes
from app.agents.socrates import Socrates
from app.agents.aristotle import Aristotle
from app.agents.leonidas import Leonidas
from app.agents.athena import Athena
from app.agents.heraclitus import Heraclitus
from app.agents.pythagoras import Pythagoras
from app.agents.solon import Solon
from app.agents.archimedes import Archimedes
from app.agents.odysseus import Odysseus
from app.agents.iapetus import Iapetus
from app.agents.astraeus import Astraeus
from app.agents.erebos import Erebos
from app.agents.phantasos import Phantasos
from app.agents.stilbon import Stilbon
from app.agents.hermes import HermesAgent
from app.agents.autopilot import Autopilot
from app.agents.genesis import Genesis
from app.agents.overmind import Overmind
from app.agents.omniscient import Omniscient
from app.agents.eternal import Eternal
from app.agents.singularity import Singularity
from app.routers import autopilot as autopilot_router
from app.routers import hermes as hermes_router
from app.routers import beast_mode as beast_mode_router
from app.routers import browser_harness_ws as browser_harness_router

council = CouncilOrchestrator()


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    await init_db()
    # Load saved integration credentials (LLM keys etc.) into runtime settings
    from app.services.connection_service import apply_saved_connections
    await apply_saved_connections()
    # Initialize Sentry if configured
    if settings.sentry_dsn:
        import sentry_sdk
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            traces_sample_rate=0.25,
        )
    # Register all AI council agents
    council.register(Plato())
    council.register(Socrates())
    council.register(Aristotle())
    council.register(Leonidas())
    council.register(Athena())
    council.register(Heraclitus())
    council.register(Pythagoras())
    council.register(Solon())
    council.register(Archimedes())
    council.register(Odysseus())
    council.register(Iapetus())
    council.register(Astraeus())
    council.register(Erebos())
    council.register(Phantasos())
    council.register(Stilbon())
    # Omega-tier agents (DeepSeek V4 Pro)
    council.register(Genesis())
    council.register(Overmind())
    council.register(Omniscient())
    council.register(Eternal())
    council.register(Singularity())
    app.state.council = council
    hermes = HermesAgent()
    hermes.council = council
    plato_set_hermes(hermes)
    app.state.hermes = hermes
    # Recover jobs that were running when the server last crashed
    await hermes.recover_jobs()
    # Warm the in-memory cache with recent job history
    await hermes.load_recent_jobs()
    app.state.autopilot = Autopilot(council=council)
    # Start the in-process job scheduler (drip campaigns, scheduled posts)
    from app.services.scheduler import scheduler
    scheduler.start()
    app.state.scheduler = scheduler
    yield
    scheduler.stop()
    app.state.autopilot.stop()
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="AI-native Agency Operating System",
    lifespan=lifespan,
)

# Request-ID middleware (first middleware to catch all requests)
class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", "")
        if not request_id:
            import uuid
            request_id = str(uuid.uuid4())[:8]
        get_request_id_filter().set_request_id(request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

# Middleware (order matters — rate limiter before CORS)
app.add_middleware(RateLimitMiddleware, max_requests=100, window_seconds=60)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

setup_error_handlers(app)

# Security headers middleware
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

app.add_middleware(SecurityHeadersMiddleware)

# Request-ID must be outermost (last added = first executed)
app.add_middleware(RequestIDMiddleware)

# Prometheus metrics endpoint (before all routers so it's unauthenticated)
prometheus_metrics_app = make_asgi_app()
app.mount("/metrics", prometheus_metrics_app)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    import time
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    http_requests_total.labels(
        method=request.method,
        endpoint=request.url.path,
        status=response.status_code,
    ).inc()
    http_request_duration.labels(
        method=request.method,
        endpoint=request.url.path,
    ).observe(duration)
    return response


# Register routers — health and webhooks first (no auth needed)
app.include_router(health.router, prefix="/api/v1", tags=["Health"])
app.include_router(webhooks.router, prefix="/api/v1/webhooks", tags=["Webhooks"])

# Auth-protected routes
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(users.router, prefix="/api/v1/users", tags=["Users"])
app.include_router(leads.router, prefix="/api/v1/leads", tags=["Leads"])
app.include_router(clients.router, prefix="/api/v1/clients", tags=["Clients"])
app.include_router(campaigns.router, prefix="/api/v1/campaigns", tags=["Campaigns"])
app.include_router(messages.router, prefix="/api/v1", tags=["Messages"])
app.include_router(finance.router, prefix="/api/v1/finance", tags=["Finance"])
app.include_router(analytics.router, prefix="/api/v1/analytics", tags=["Analytics"])
app.include_router(agents.router, prefix="/api/v1/agents", tags=["Agents"])
app.include_router(knowledge.router, prefix="/api/v1/knowledge", tags=["Knowledge"])
app.include_router(calendar.router, prefix="/api/v1/calendar", tags=["Calendar"])
app.include_router(tasks.router, prefix="/api/v1/tasks", tags=["Tasks"])
app.include_router(automation.router, prefix="/api/v1/automation", tags=["Automation"])
app.include_router(autopilot_router.router, prefix="/api/v1/autopilot", tags=["Autopilot"])
app.include_router(connections.router, prefix="/api/v1/connections", tags=["Connections"])
app.include_router(browser_harness_router.router)
app.include_router(hermes_router.router, prefix="/api/v1/hermes", tags=["Hermes"])
app.include_router(chat.router, prefix="/api/v1", tags=["Chat"])
app.include_router(cleanup.router, prefix="/api/v1/cleanup", tags=["Cleanup"])
app.include_router(beast_mode_router.router)
app.include_router(lead_lists.router, prefix="/api/v1/lead-lists", tags=["Lead Lists"])
app.include_router(notifications.router, prefix="/api/v1/notifications", tags=["Notifications"])


@app.get("/")
async def root():
    return {"app": settings.app_name, "version": "0.1.0", "status": "running"}

# Normalize API trailing slashes — ensures /api/v1/leads and /api/v1/leads/ both work
# without issuing a 307 redirect (which would use http:// and be blocked by Chrome)
from starlette.middleware.base import BaseHTTPMiddleware
class NormalizeSlashMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.scope.get("path", "")
        if path.startswith("/api/") and not path.endswith("/") and "." not in path.rsplit("/", 1)[-1]:
            request.scope["path"] = path + "/"
        return await call_next(request)

app.add_middleware(NormalizeSlashMiddleware)
