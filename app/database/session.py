import logging
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool
from app.config import settings

logger = logging.getLogger(__name__)

# Use Supabase/PostgreSQL when configured; otherwise fall back to a local
# SQLite database so the app runs natively without external services.
# The data/ subdirectory is mounted as a Railway Volume for persistence.
_data_dir = Path(__file__).resolve().parents[2] / "data"
_data_dir.mkdir(exist_ok=True)
_sqlite_path = _data_dir / "socrates.db"
DATABASE_URL = settings.supabase_db_url or f"sqlite+aiosqlite:///{_sqlite_path}"

IS_SQLITE = DATABASE_URL.startswith("sqlite")

if IS_SQLITE:
    # NullPool: fresh connection per session, never reused.
    # Prevents a failed transaction on one request from tainting the next.
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        poolclass=NullPool,
        connect_args={"timeout": 30, "check_same_thread": False},
    )
    logger.info(f"Database: SQLite fallback at {_sqlite_path}")
else:
    engine = create_async_engine(
        DATABASE_URL,
        echo=settings.debug,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_pre_ping=True,
    )

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Create all tables if they don't exist (SQLite/native mode).

    PostgreSQL deployments use Alembic migrations instead; create_all is a
    no-op for tables that already exist.
    """
    from app.database.models import Base, User, Organization
    from sqlalchemy import text

    async with engine.begin() as conn:
        if IS_SQLITE:
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA busy_timeout=30000"))
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema ensured (create_all)")

    # ── Migrations (columns added after initial schema) ──────────────
    try:
        if IS_SQLITE:
            for col, coltype in [
                ("email_verified", "BOOLEAN DEFAULT 0"),
                ("email_verify_token", "VARCHAR(255)"),
                ("email_verify_token_expires", "TIMESTAMP"),
            ]:
                try:
                    await conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {coltype}"))
                except Exception:
                    pass  # Column already exists
        else:
            await conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN DEFAULT FALSE"
            ))
            await conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verify_token VARCHAR(255)"
            ))
            await conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verify_token_expires TIMESTAMP WITH TIME ZONE"
            ))
        logger.info("Database migrations applied (email_verified columns)")
    except Exception as e:
        logger.warning(f"Could not run migrations: {e}")

    # Seed default org if not exists (needed for beast mode / dev flows)
    try:
        async with async_session() as session:
            result = await session.execute(
                text("SELECT id FROM organizations WHERE id = '00000000-0000-0000-0000-000000000001'")
            )
            if not result.fetchone():
                session.add(Organization(
                    id="00000000-0000-0000-0000-000000000001",
                    name="Default Org",
                    slug="default",
                ))
                await session.commit()
                logger.info("Seeded default organization")
    except Exception as e:
        logger.warning(f"Could not seed default organization: {e}")

    # Seed dev user if not exists (for local development without Clerk)
    try:
        from sqlalchemy import select
        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.email == "dev@localhost")
            )
            if not result.scalar_one_or_none():
                session.add(User(
                    id="00000000-0000-0000-0000-000000000010",
                    email="dev@localhost",
                    name="Developer",
                    clerk_id="00000000-0000-0000-0000-000000000010",
                ))
                await session.commit()
                logger.info("Seeded dev user")
    except Exception as e:
        logger.warning(f"Could not seed dev user: {e}")


async def get_db() -> AsyncSession:
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
