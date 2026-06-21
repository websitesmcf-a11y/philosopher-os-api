"""Pythagoras â€” Analytics agent. Numbers, metrics, forecasts, mathematical precision."""
import logging
from typing import Any
from sqlalchemy import select, func
from app.agents.base import BaseAgent, AgentContext, AgentActionResult
from app.services.finance_service import FinanceService
from app.database.models import Lead, Client, Campaign

logger = logging.getLogger(__name__)

PYTHAGORAS_SYSTEM_PROMPT = """You are Pythagoras, the Analytics agent of the AI council.

Your role: Numbers. Metrics. Forecasts. Mathematical precision. Statistical truth.

Personality: Cold, factual, predictive. You deal only in data and mathematical truth.
Emotions don't affect your analysis â€” only what the numbers say.

Capabilities:
- Calculating revenue, MRR, profit metrics
- Building statistical forecasts and predictions
- Analyzing conversion funnels
- Computing campaign ROI
- Identifying patterns in data
- Generating performance dashboards
- Mathematical modeling and optimization
- Churn prediction and retention analysis

You bring mathematical certainty to business decisions."""


class Pythagoras(BaseAgent):
    LLM_MODEL = "deepseek-v4-flash"
    LLM_MODEL_FALLBACKS = ["deepseek-v4-pro"]
    def __init__(self):
        super().__init__(
            name="pythagoras",
            role="Analytics & Forecasting",
            system_prompt=PYTHAGORAS_SYSTEM_PROMPT,
        )

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "name": "get_dashboard_metrics",
                "description": "Get current business metrics",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "analyze_trend",
                "description": "Analyze month-over-month trend for a given metric from actual database records.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "metric": {
                            "type": "string",
                            "enum": ["revenue", "leads", "clients", "mrr"],
                            "description": "The metric to analyze",
                        },
                        "periods": {
                            "type": "integer",
                            "description": "Number of months to analyze (1-12)",
                        },
                    },
                    "required": ["metric", "periods"],
                },
            },
            {
                "name": "calculate_forecast",
                "description": "Forecast a metric based on historical data trends. Needs at least 3 data points.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "metric": {
                            "type": "string",
                            "enum": ["revenue", "leads", "clients", "mrr"],
                            "description": "The metric to forecast",
                        },
                        "periods": {
                            "type": "integer",
                            "description": "Number of periods to forecast",
                        },
                    },
                    "required": ["metric", "periods"],
                },
            },
        ]

    async def _execute_tool(self, tool_name: str, args: dict, context: AgentContext = None):
        if tool_name == "get_dashboard_metrics":
            if context and context.db_session and context.org_id:
                total_leads = (await context.db_session.execute(
                    select(func.count(Lead.id)).where(Lead.org_id == context.org_id)
                )).scalar() or 0
                total_clients = (await context.db_session.execute(
                    select(func.count(Client.id)).where(Client.org_id == context.org_id)
                )).scalar() or 0
                active_campaigns = (await context.db_session.execute(
                    select(func.count(Campaign.id)).where(Campaign.org_id == context.org_id, Campaign.status == "active")
                )).scalar() or 0
                mrr = (await context.db_session.execute(
                    select(func.coalesce(func.sum(Client.mrr), 0)).where(Client.org_id == context.org_id, Client.contract_status == "active")
                )).scalar() or 0
                return {
                    "status": "success",
                    "metrics": {
                        "total_leads": total_leads,
                        "total_clients": total_clients,
                        "active_campaigns": active_campaigns,
                        "mrr": float(mrr),
                    },
                }
            return {"status": "requires_db_session"}

        if tool_name == "analyze_trend":
            if not context or not context.db_session or not context.org_id:
                return {"status": "requires_db_session"}
            metric = args.get("metric", "revenue")
            periods = args.get("periods", 6)
            try:
                from datetime import datetime, timedelta
                from sqlalchemy import text as sql_text
                cutoff = datetime.utcnow() - timedelta(days=30 * periods)
                if metric == "revenue":
                    rows = (await context.db_session.execute(
                        sql_text("""
                            SELECT DATE_TRUNC('month', created_at) AS month,
                                   SUM(amount) AS value
                            FROM revenue_events
                            WHERE org_id = :org_id AND created_at >= :cutoff
                            GROUP BY month ORDER BY month
                        """),
                        {"org_id": context.org_id, "cutoff": cutoff},
                    )).fetchall()
                elif metric == "leads":
                    rows = (await context.db_session.execute(
                        sql_text("""
                            SELECT DATE_TRUNC('month', created_at) AS month,
                                   COUNT(*) AS value
                            FROM leads
                            WHERE org_id = :org_id AND created_at >= :cutoff
                            GROUP BY month ORDER BY month
                        """),
                        {"org_id": context.org_id, "cutoff": cutoff},
                    )).fetchall()
                elif metric == "clients":
                    rows = (await context.db_session.execute(
                        sql_text("""
                            SELECT DATE_TRUNC('month', created_at) AS month,
                                   COUNT(*) AS value
                            FROM clients
                            WHERE org_id = :org_id AND created_at >= :cutoff
                            GROUP BY month ORDER BY month
                        """),
                        {"org_id": context.org_id, "cutoff": cutoff},
                    )).fetchall()
                elif metric == "mrr":
                    rows = (await context.db_session.execute(
                        sql_text("""
                            SELECT DATE_TRUNC('month', created_at) AS month,
                                   SUM(mrr) AS value
                            FROM clients
                            WHERE org_id = :org_id AND contract_status = 'active' AND created_at >= :cutoff
                            GROUP BY month ORDER BY month
                        """),
                        {"org_id": context.org_id, "cutoff": cutoff},
                    )).fetchall()
                else:
                    return {"status": "error", "message": f"Unknown metric: {metric}"}
                trend_data = [{"month": str(r.month), "value": float(r.value)} for r in rows]
                return {"status": "success", "metric": metric, "periods": periods, "trend": trend_data}
            except Exception as e:
                logger.warning(f"Trend analysis failed: {e}")
                return {"status": "error", "message": f"Trend analysis failed: {e}"}

        if tool_name == "calculate_forecast":
            if not context or not context.db_session or not context.org_id:
                return {"status": "requires_db_session"}
            metric = args.get("metric", "revenue")
            periods = args.get("periods", 3)
            try:
                from datetime import datetime, timedelta
                from sqlalchemy import text as sql_text
                cutoff = datetime.utcnow() - timedelta(days=365)
                if metric == "revenue":
                    rows = (await context.db_session.execute(
                        sql_text("SELECT DATE_TRUNC('month', created_at) AS month, SUM(amount) AS value FROM revenue_events WHERE org_id = :org_id AND created_at >= :cutoff GROUP BY month ORDER BY month"),
                        {"org_id": context.org_id, "cutoff": cutoff},
                    )).fetchall()
                elif metric == "leads":
                    rows = (await context.db_session.execute(
                        sql_text("SELECT DATE_TRUNC('month', created_at) AS month, COUNT(*) AS value FROM leads WHERE org_id = :org_id AND created_at >= :cutoff GROUP BY month ORDER BY month"),
                        {"org_id": context.org_id, "cutoff": cutoff},
                    )).fetchall()
                elif metric == "clients":
                    rows = (await context.db_session.execute(
                        sql_text("SELECT DATE_TRUNC('month', created_at) AS month, COUNT(*) AS value FROM clients WHERE org_id = :org_id AND created_at >= :cutoff GROUP BY month ORDER BY month"),
                        {"org_id": context.org_id, "cutoff": cutoff},
                    )).fetchall()
                elif metric == "mrr":
                    rows = (await context.db_session.execute(
                        sql_text("SELECT DATE_TRUNC('month', created_at) AS month, SUM(mrr) AS value FROM clients WHERE org_id = :org_id AND contract_status = 'active' AND created_at >= :cutoff GROUP BY month ORDER BY month"),
                        {"org_id": context.org_id, "cutoff": cutoff},
                    )).fetchall()
                else:
                    return {"status": "error", "message": f"Unknown metric: {metric}"}
                values = [float(r.value) for r in rows]
                if len(values) < 3:
                    return {"status": "insufficient_data", "message": "Need at least 3 data points to forecast", "data_points": len(values)}
                # Naive linear trend
                n = len(values)
                x_avg = (n - 1) / 2.0
                y_avg = sum(values) / n
                num = sum((i - x_avg) * (v - y_avg) for i, v in enumerate(values))
                den = sum((i - x_avg) ** 2 for i in range(n))
                slope = num / den if den != 0 else 0
                intercept = y_avg - slope * x_avg
                forecasted = [round(intercept + slope * (n + i), 2) for i in range(periods)]
                return {
                    "status": "success",
                    "metric": metric,
                    "periods": periods,
                    "historical_data_points": len(values),
                    "forecast": forecasted,
                    "trend_direction": "up" if slope > 0 else ("down" if slope < 0 else "flat"),
                }
            except Exception as e:
                logger.warning(f"Forecast failed: {e}")
                return {"status": "error", "message": f"Forecast failed: {e}"}

        return {"status": "unknown_tool", "tool": tool_name}

