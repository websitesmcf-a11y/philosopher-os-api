# Socrates AI — Production Deployment Guide

## Prerequisites

- Docker & Docker Compose v2
- VPS or Coolify instance with Docker support
- Domain name (optional, for HTTPS)

## Environment Variables

Copy `.env.example` to `.env` and fill in:

### Required
```
SUPABASE_DB_URL=postgresql+asyncpg://user:pass@host:5432/socrates
REDIS_URL=redis://redis:6379
```

### LLM Provider (at least one)
```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

### Optional
```
SENTRY_DSN=https://...
RESEND_API_KEY=re_...
CLERK_SECRET_KEY=sk_...
WHATSAPP_WEBHOOK_SECRET=...
WA_BOT_URL=http://wa_bot_host:8088
ENVIRONMENT=production
```

## Coolify Deployment

1. **Create a new Docker Compose resource** in Coolify
2. Point it to the `docker/docker-compose.yml` file
3. Set the following environment variables in Coolify:
   - All from the Required section above
   - Set `ENVIRONMENT=production`
4. Configure the domain → point to `web` service port 3000
5. Deploy — Coolify handles certs, DNS, and auto-updates

## Manual VPS Deployment

```bash
# Clone the repo
git clone https://github.com/your-org/socrates-ai.git
cd socrates-ai

# Create .env file
cp .env.example .env
nano .env  # fill in secrets

# Start all services
cd docker
docker compose up -d

# Check health
curl http://localhost:8000/api/v1/health/liveness
curl http://localhost:3000  # frontend
```

## Post-Deploy

```bash
# Run database migrations
docker compose exec api alembic upgrade head

# Seed initial data (optional)
docker compose exec api python -m app.scripts.seed
```

## Monitoring

- **Metrics**: `http://your-domain.com/metrics` — Prometheus-formatted
- **Health**: `http://your-domain.com/api/v1/health/liveness`
- **Sentry**: Errors automatically reported if `SENTRY_DSN` is set

## Backups

Postgres data is in the `postgres-data` Docker volume:
```bash
docker compose exec db pg_dump -U postgres socrates > backup_$(date +%Y%m%d).sql
```

## Scaling

- Increase `WEB_CONCURRENCY` env var for the API (more uvicorn workers)
- Add more `worker` service replicas in docker-compose for Celery
- Tweak `celery_concurrency` for CPU-bound tasks
