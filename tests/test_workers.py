"""Tests for Celery worker tasks in eager mode."""

from unittest.mock import patch
import pytest


@pytest.mark.asyncio
async def test_outreach_send_whatsapp():
    """WhatsApp outreach task with mock."""
    with patch("app.workers.outreach.send_whatsapp_message") as mock_task:
        mock_task.delay.return_value.get.return_value = {"status": "sent", "message_id": "msg_123"}
        result = mock_task.delay("+1234567890", "Hello from Socrates").get()
        assert result["status"] == "sent"
        assert result["message_id"] == "msg_123"


@pytest.mark.asyncio
async def test_outreach_campaign_drip():
    """Campaign drip task with mock."""
    with patch("app.workers.outreach.execute_campaign_drip") as mock_task:
        mock_task.delay.return_value.get.return_value = {"status": "completed", "sent": 10}
        result = mock_task.delay("campaign_123").get()
        assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_memory_index_embedding():
    """Memory indexing task with mock."""
    with patch("app.workers.memory_index.index_message_embedding") as mock_task:
        mock_task.delay.return_value.get.return_value = {"status": "indexed", "embedding_id": "emb_123"}
        result = mock_task.delay("msg_456").get()
        assert result["status"] == "indexed"


@pytest.mark.asyncio
async def test_analytics_compute_daily_metrics():
    """Daily metrics computation returns results."""
    with patch("app.workers.analytics.compute_daily_metrics") as mock_task:
        mock_task.delay.return_value.get.return_value = {
            "status": "completed",
            "new_leads": 5,
            "revenue": 15000,
            "tasks_completed": 12,
        }
        result = mock_task.delay().get()
        assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_report_generation():
    """Report generation returns content."""
    with patch("app.workers.report_gen.generate_client_report") as mock_task:
        mock_task.delay.return_value.get.return_value = {
            "status": "completed",
            "report_url": "/reports/client_123.pdf",
        }
        result = mock_task.delay("client_123", "monthly").get()
        assert result["status"] == "completed"
