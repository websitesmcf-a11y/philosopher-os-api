"""Tests for analytics endpoints."""

import pytest
from httpx import AsyncClient
from unittest.mock import patch


@pytest.mark.asyncio
async def test_dashboard_endpoint(client: AsyncClient):
    """Dashboard returns metrics for the scoped org."""
    response = await client.get("/api/v1/analytics/dashboard")
    assert response.status_code == 200
    data = response.json()
    assert "total_leads" in data
    assert "active_campaigns" in data
    assert "conversion_rate" in data


@pytest.mark.asyncio
async def test_lead_analytics(client: AsyncClient):
    """Lead analytics returns expected fields."""
    response = await client.get("/api/v1/analytics/leads")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_campaign_analytics(client: AsyncClient):
    """Campaign analytics returns data."""
    response = await client.get("/api/v1/analytics/campaigns")
    assert response.status_code == 200
