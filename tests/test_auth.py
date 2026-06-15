"""Tests for auth endpoints (local dev-mode login without Clerk)."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient):
    response = await client.post("/api/v1/auth/login", json={
        "email": "admin@socrates.ai",
        "password": "admin123",
    })
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_invalid_password(client: AsyncClient):
    response = await client.post("/api/v1/auth/login", json={
        "email": "admin@socrates.ai",
        "password": "wrongpassword",
    })
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_returns_user_in_dev_mode(client: AsyncClient):
    """Without Clerk configured, /me returns the default dev identity."""
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 200
    data = response.json()
    assert "email" in data


@pytest.mark.asyncio
async def test_me_requires_auth_when_clerk_configured(client: AsyncClient):
    """With Clerk configured, /me without a token is rejected."""
    from app.config import settings
    original = settings.clerk_secret_key
    settings.clerk_secret_key = "sk_test_fake"
    try:
        response = await client.get("/api/v1/auth/me")
        assert response.status_code == 401
    finally:
        settings.clerk_secret_key = original


@pytest.mark.asyncio
async def test_logout(client: AsyncClient):
    response = await client.post("/api/v1/auth/logout")
    assert response.status_code == 200
