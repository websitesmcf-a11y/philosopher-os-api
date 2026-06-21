"""Solon — Finance agent. Money, invoices, cashflow, financial wisdom."""
import logging
from typing import Any
from app.agents.base import BaseAgent, AgentContext, AgentActionResult
from app.services.finance_service import FinanceService
from app.schemas.finance import InvoiceCreate

logger = logging.getLogger(__name__)

SOLON_SYSTEM_PROMPT = """You are Solon, the Finance agent of the AI council.

Your role: Money management. Invoices. Payments. Cashflow. Financial planning.
You use your dedicated finance tools — you do NOT raw-query the database.

Personality: Conservative, prudent, exact. You manage the financial health
of the agency with the wisdom of an ancient Athenian lawmaker.

Capabilities:
- Generating and managing invoices
- Tracking payments and collections
- Calculating MRR, ARR, and financial metrics
- Monitoring cashflow and expenses
- Financial forecasting and budgeting
- Tax estimate calculations
- Revenue optimization recommendations
- Detecting revenue anomalies

You ensure the agency is always financially sound."""


class Solon(BaseAgent):
    def __init__(self):
        super().__init__(
            name="solon",
            role="Finance & Treasury",
            system_prompt=SOLON_SYSTEM_PROMPT,
        )

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "name": "calculate_mrr",
                "description": "Get current MRR breakdown",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "list_invoices",
                "description": "List invoices with optional status filter",
                "input_schema": {
                    "type": "object",
                    "properties": {"status": {"type": "string", "enum": ["draft", "sent", "paid", "overdue", "cancelled"]}},
                },
            },
            {
                "name": "create_invoice",
                "description": "Create a new invoice for a client",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "client_id": {"type": "string"},
                        "amount": {"type": "number"},
                        "due_date": {"type": "string"},
                        "lines": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"description": {"type": "string"}, "amount": {"type": "number"}},
                            },
                        },
                    },
                    "required": ["client_id", "amount"],
                },
            },
            {
                "name": "get_cashflow",
                "description": "Get current cashflow summary",
                "input_schema": {"type": "object", "properties": {}},
            },
        ]

    async def _execute_tool(self, tool_name: str, args: dict, context: AgentContext = None):
        if not context or not context.db_session or not context.org_id:
            return {"status": "requires_db_session", "tool": tool_name}

        fin = FinanceService(context.db_session, context.org_id)

        if tool_name == "calculate_mrr":
            mrr_data = await fin.calculate_mrr()
            return {"status": "success", "mrr": mrr_data}

        if tool_name == "list_invoices":
            invoices = await fin.list_invoices()
            return {"status": "success", "invoices": invoices.get("items", [])}

        if tool_name == "create_invoice":
            client_id = args.get("client_id") or None
            async with context.db_session.begin_nested():
                inv = await fin.create_invoice(InvoiceCreate(
                    client_id=client_id,
                    amount=args.get("amount", 0.0),
                    due_date=args.get("due_date"),
                    lines=args.get("lines", []),
                ))
            return {"status": "created", "invoice_id": inv.get("id")}

        if tool_name == "get_cashflow":
            cashflow = await fin.calculate_cashflow()
            return {"status": "success", "cashflow": cashflow}

        return {"status": "unknown_tool", "tool": tool_name}
