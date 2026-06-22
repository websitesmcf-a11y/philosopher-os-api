"""Auth dependencies: Clerk JWT verification with TTL-cached JWKS, user + org extraction."""

import time
import httpx
import logging
from fastapi import Request, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.config import settings
from app.database.session import get_db
from app.database.models import OrgMember

logger = logging.getLogger(__name__)
security_scheme = HTTPBearer(auto_error=False)

# JWKS cache with TTL
_jwks_cache: dict | None = None
_jwks_cache_time: float = 0
JWKS_CACHE_TTL = 3600  # 1 hour


async def get_jwks() -> dict:
    global _jwks_cache, _jwks_cache_time
    now = time.time()
    if _jwks_cache and (now - _jwks_cache_time) < JWKS_CACHE_TTL:
        return _jwks_cache
    async with httpx.AsyncClient() as client:
        resp = await client.get(settings.clerk_jwks_url)  # type: ignore
        resp.raise_for_status()
        _jwks_cache = resp.json()
        _jwks_cache_time = now
    return _jwks_cache


async def verify_clerk_token(token: str) -> dict | None:
    """Verify a Clerk JWT and return the payload."""
    try:
        jwks = await get_jwks()
        from jose import jwt

        headers = jwt.get_unverified_headers(token)
        kid = headers.get("kid")
        if not kid:
            return None

        key_data = None
        for k in jwks.get("keys", []):
            if k.get("kid") == kid:
                key_data = k
                break

        if not key_data:
            return None

        payload = jwt.decode(
            token,
            key_data,
            algorithms=["RS256"],
            audience=settings.clerk_jwks_url,
            options={"verify_aud": bool(settings.clerk_webhook_secret)},
        )
        return payload
    except Exception as e:
        logger.warning(f"Token verification failed: {e}")
        return None


# Default identity when running without Clerk in non-production environments.
# Matches the org seeded by scripts/seed.py so the dashboard shows real data.
DEV_USER = {
    "id": "00000000-0000-0000-0000-000000000010",
    "email": "dev@localhost",
    "name": "Developer",
    "org_id": "00000000-0000-0000-0000-000000000001",
}


def _auth_disabled() -> bool:
    """True when Clerk isn't configured — fall back to local auth so the app works
    without a Clerk account in any environment (dev or production)."""
    return not settings.clerk_secret_key


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
) -> dict:
    """Dependency that extracts and verifies the current user from Clerk JWT."""
    if _auth_disabled():
        return dict(DEV_USER)

    if not credentials:
        raise HTTPException(status_code=401, detail="Authentication required")

    payload = await verify_clerk_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return {
        "id": payload.get("sub", ""),
        "email": payload.get("email", ""),
        "name": f"{payload.get('given_name', '')} {payload.get('family_name', '')}".strip(),
        "org_id": payload.get("org_id", ""),
    }


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
) -> dict | None:
    """Like get_current_user but doesn't error if no token."""
    if not credentials:
        return None
    payload = await verify_clerk_token(credentials.credentials)
    if payload:
        return {
            "id": payload.get("sub", ""),
            "email": payload.get("email", ""),
            "name": f"{payload.get('given_name', '')} {payload.get('family_name', '')}".strip(),
            "org_id": payload.get("org_id", ""),
        }
    return None


async def get_current_org(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> str:
    """Resolve the user's org_id from JWT or fall back to OrgMember table."""
    if user.get("org_id"):
        return user["org_id"]
    result = await db.execute(
        select(OrgMember.org_id).where(OrgMember.user_id == user["id"]).limit(1)
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=403, detail="User has no organization")
    return str(row)
