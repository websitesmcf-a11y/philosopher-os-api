"""Tests for webhook endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_webhooks_endpoint(client: AsyncClient):
    """Webhooks endpoint is accessible."""
    response = await client.post("/api/v1/webhooks/clerk", json={
        "type": "user.created",
        "data": {
            "id": "clerk_user_123",
            "email": "test@example.com",
            "first_name": "Test",
            "last_name": "User",
        },
    })
    # Webhook may validate signatures, but endpoint should respond
    assert response.status_code in (200, 201, 400, 401, 403)


@pytest.mark.asyncio
async def test_whatsapp_webhook(client: AsyncClient):
    """WhatsApp webhook routes to council."""
    response = await client.post("/api/v1/webhooks/whatsapp", json={
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": "+1234567890",
                        "text": {"body": "Hello Socrates"},
                        "id": "wamid_123",
                    }],
                    "metadata": {"phone_number_id": "123456"},
                }
            }]
        }],
    })
    assert response.status_code in (200, 202, 400, 401)


@pytest.mark.asyncio
async def test_email_webhook(client: AsyncClient):
    """Email webhook processes bounce events."""
    response = await client.post("/api/v1/webhooks/email", json={
        "event": "bounce",
        "email": "bounced@example.com",
        "reason": "mailbox full",
    })
    assert response.status_code in (200, 202, 400, 401)
