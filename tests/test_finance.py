"""Tests for finance endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_dashboard_metrics(client: AsyncClient):
    response = await client.get("/api/v1/analytics/dashboard")
    assert response.status_code == 200
    data = response.json()
    assert "total_leads" in data
    assert "total_clients" in data


@pytest.mark.asyncio
async def test_mrr_endpoint(client: AsyncClient):
    response = await client.get("/api/v1/finance/mrr", params={"period": "monthly"})
    assert response.status_code == 200
    data = response.json()
    assert "total_mrr" in data
    assert "new_business" in data
    assert "churn" in data


@pytest.mark.asyncio
async def test_invoices_endpoint(client: AsyncClient):
    response = await client.get("/api/v1/finance/invoices")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_cashflow_endpoint(client: AsyncClient):
    response = await client.get("/api/v1/finance/cashflow")
    assert response.status_code == 200
    data = response.json()
    assert "total_revenue" in data
    assert "total_expenses" in data
    assert "net_cashflow" in data


@pytest.mark.asyncio
async def test_revenue_query(client: AsyncClient):
    response = await client.get("/api/v1/finance/revenue")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_expenses_query(client: AsyncClient):
    response = await client.get("/api/v1/finance/expenses")
    assert response.status_code == 200
