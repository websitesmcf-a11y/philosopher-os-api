from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # App
    app_name: str = "Socrates AI"
    environment: str = "development"
    debug: bool = True
    api_url: Optional[str] = "http://localhost:8000"
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
        "https://philosopher-os.vercel.app",
        "https://philosopher-os-frontend.vercel.app",
        "https://philosopher-os-frontend3.vercel.app",
        "https://philosopher-os-frontend2.vercel.app",
        "https://philosopher-os-frontend-git-main-websitesmcf-8823s-projects.vercel.app",
    ]
    log_level: str = "INFO"
    log_format: str = "json"  # "json" or "text"
    enable_request_id: bool = True

    # Supabase
    supabase_url: Optional[str] = None
    supabase_anon_key: Optional[str] = None
    supabase_service_role_key: Optional[str] = None
    supabase_db_url: Optional[str] = None

    # Clerk Auth
    clerk_secret_key: Optional[str] = None
    clerk_webhook_secret: Optional[str] = None
    clerk_jwks_url: Optional[str] = "https://api.clerk.com/v1/jwks"

    # Redis
    redis_url: str = ""

    # Celery
    celery_broker_url: str = ""
    celery_result_backend: str = ""
    worker_concurrency: int = 4

    # LLM Providers
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    ollama_url: str = "http://localhost:11434"
    default_llm_provider: str = "openrouter"  # openrouter | anthropic | openai | deepseek | ollama | auto
    default_llm_model: str = "claude-sonnet-4-20250514"
    deepseek_model: str = "deepseek-chat"
    embedding_model: str = "text-embedding-3-small"

    # Resend (Email) — legacy; SMTP inboxes below are the primary email path
    resend_api_key: Optional[str] = None

    # SMTP (Email) — primary inbox applied from saved connections at startup
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None

    # Sentry
    sentry_dsn: Optional[str] = None

    # Encryption
    encryption_key: Optional[str] = None

    # Local admin login (used only when Clerk is not configured, non-production)
    dev_admin_email: str = "admin@socrates.ai"
    dev_admin_password: str = "admin123"

    # WhatsApp
    whatsapp_session_path: str = "./.wwebjs_auth"
    wa_bot_url: str = ""
    whatsapp_webhook_secret: Optional[str] = None

    # Database
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # Integrations
    browser_harness_url: Optional[str] = None
    browser_harness_api_key: Optional[str] = None
    hermes_api_url: Optional[str] = None
    hermes_api_key: Optional[str] = None
    storage_backend: str = "local"  # "local" or "s3"

    # Feature flags
    feature_flags: dict = {}

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def effective_debug(self) -> bool:
        """Disable debug in production regardless of config."""
        return self.debug if not self.is_production else False

    @property
    def effective_cors_origins(self) -> list[str]:
        if self.is_production and self.cors_origins == ["http://localhost:3000", "http://localhost:3001"]:
            # In production, require explicit CORS config via env
            return self.cors_origins
        return self.cors_origins


settings = Settings()
