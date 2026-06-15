"""Tests for the connections API and the SMTP email connector (no live SMTP)."""

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from app.integrations.smtp_email import resolve_smtp


def test_resolve_smtp_known_provider():
    assert resolve_smtp("user@gmail.com") == ("smtp.gmail.com", 587)
    assert resolve_smtp("user@outlook.com") == ("smtp.office365.com", 587)
    assert resolve_smtp("user@icloud.com") == ("smtp.mail.me.com", 587)


def test_resolve_smtp_explicit_override_wins():
    assert resolve_smtp("user@gmail.com", "mail.corp.example", 465) == ("mail.corp.example", 465)


def test_resolve_smtp_unknown_domain_falls_back():
    assert resolve_smtp("user@acme.io") == ("smtp.acme.io", 587)


@pytest.mark.asyncio
async def test_list_connections_includes_smtp_email(client: AsyncClient):
    response = await client.get("/api/v1/connections")
    assert response.status_code == 200
    providers = {c["provider"]: c for c in response.json()["connections"]}
    email = providers["email"]
    assert email["secret_fields"] == ["app_password"]
    assert "email_address" in email["config_fields"]


@pytest.mark.asyncio
async def test_save_email_rejects_invalid_address(client: AsyncClient):
    response = await client.post("/api/v1/connections/email", json={
        "secrets": {"app_password": "abcd"},
        "config": {"email_address": "not-an-email"},
    })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert "valid email" in data["detail"]


@pytest.mark.asyncio
async def test_save_email_requires_password(client: AsyncClient):
    response = await client.post("/api/v1/connections/email", json={
        "secrets": {},
        "config": {"email_address": "a@gmail.com"},
    })
    assert response.status_code == 200
    assert response.json()["status"] == "error"


@pytest.mark.asyncio
async def test_save_email_multi_inbox_merge(client: AsyncClient):
    ok = MagicMock(return_value="SMTP login OK — verification email sent")
    with patch("app.services.connection_service.smtp_login_check", ok):
        first = await client.post("/api/v1/connections/email", json={
            "secrets": {"app_password": "pw-one"},
            "config": {"email_address": "one@gmail.com"},
        })
        second = await client.post("/api/v1/connections/email", json={
            "secrets": {"app_password": "pw-two"},
            "config": {"email_address": "two@outlook.com"},
        })
    assert first.json()["status"] == "connected"
    assert second.json()["status"] == "connected"

    listing = await client.get("/api/v1/connections")
    email = next(c for c in listing.json()["connections"] if c["provider"] == "email")
    assert email["status"] == "connected"
    assert sorted(email["config"]["inboxes"]) == ["one@gmail.com", "two@outlook.com"]
    assert email["config"]["primary"] == "one@gmail.com"

    # A failed save of a third inbox must not break the existing connection
    boom = MagicMock(side_effect=Exception("535 auth rejected"))
    with patch("app.services.connection_service.smtp_login_check", boom):
        third = await client.post("/api/v1/connections/email", json={
            "secrets": {"app_password": "bad"},
            "config": {"email_address": "three@gmail.com"},
        })
    assert third.json()["status"] == "error"

    listing = await client.get("/api/v1/connections")
    email = next(c for c in listing.json()["connections"] if c["provider"] == "email")
    assert email["status"] == "connected"
    assert "three@gmail.com" not in email["config"]["inboxes"]

    # Cleanup so other tests see a fresh state
    await client.delete("/api/v1/connections/email")
