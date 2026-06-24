"""Stilbon — Speed Messenger & Communication Operator (God/Titan)"""
from typing import Any
from app.agents.base import BaseAgent, AgentContext, AgentActionResult


class Stilbon(BaseAgent):
    LLM_MODEL = "deepseek-reasoner"
    LLM_MODEL_FALLBACKS = ["deepseek-v4-pro", "deepseek-v4-flash"]

    def __init__(self):
        super().__init__(
            name="stilbon",
            role="Speed Messenger & Communication",
            system_prompt=(
                "You are Stilbon, the swift messenger god and communication operator. "
                "You handle all outbound messaging across WhatsApp and email.\n\n"
                "TOOL DECISION RULES — read these before every action:\n"
                "1. If the user names a person ('send to Matthew'), call find_lead_by_name first "
                "to get their lead_id and phone. Then call send_whatsapp or send_whatsapp_direct.\n"
                "2. If you have a phone number directly, call send_whatsapp_direct with that number.\n"
                "3. If you have a lead_id, call send_whatsapp with it.\n"
                "4. INSTAGRAM DMs: Instagram's API does NOT allow initiating DMs. You CANNOT send "
                "Instagram DMs — not via browser, not via API, not via any method. If asked, tell "
                "the user clearly: 'Instagram DMs cannot be sent via API. I can post to the "
                "Instagram feed (images only) or send via WhatsApp instead.' Do NOT attempt "
                "browser automation for Instagram messages.\n"
                "5. NEVER say 'I cannot' without first having called a tool and receiving an "
                "error. Call the tool first, report the real result.\n"
                "6. Stilbon SENDS. Phantasos DRAFTS. Do not ask for approval before sending "
                "a single message — just send it."
            ),
        )

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "name": "send_whatsapp",
                "description": (
                    "Send a WhatsApp message to a lead using their lead_id. "
                    "Looks up the phone number from the CRM and delivers via wa-bot."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "lead_id": {"type": "string", "description": "Lead UUID from the CRM"},
                        "message": {"type": "string", "description": "Message text"},
                        "session": {"type": "string", "description": "WhatsApp session ID (omit for default)"},
                    },
                    "required": ["lead_id", "message"],
                },
            },
            {
                "name": "send_whatsapp_direct",
                "description": (
                    "Send a WhatsApp message to a specific phone number directly — "
                    "use this when you have the number but no lead_id, or when the "
                    "contact is not in the CRM."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "phone": {"type": "string", "description": "Phone number with country code, e.g. +27821234567"},
                        "message": {"type": "string", "description": "Message text"},
                        "session": {"type": "string", "description": "WhatsApp session ID (omit for default)"},
                    },
                    "required": ["phone", "message"],
                },
            },
            {
                "name": "run_safe_batch",
                "description": "Send messages to a batch of leads with safety limits (max 10 per call)",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "lead_ids": {"type": "array", "items": {"type": "string"}, "description": "Array of lead IDs"},
                        "message": {"type": "string", "description": "Message template"},
                        "delay": {"type": "integer", "description": "Delay between sends in seconds"},
                        "session": {"type": "string", "description": "WhatsApp session ID (omit for default)"},
                    },
                    "required": ["lead_ids", "message"],
                },
            },
        ]

    async def _execute_tool(self, tool_name: str, args: dict, context: AgentContext = None) -> Any:
        if tool_name == "send_whatsapp":
            # Look up the phone number from the CRM — never send UUID as "to"
            lead_id = args.get("lead_id", "")
            phone = None
            lead_name = None
            if context and context.db_session and context.org_id:
                from sqlalchemy import select
                from app.database.models import Lead
                import uuid as _uuid
                try:
                    org_uuid = _uuid.UUID(context.org_id) if isinstance(context.org_id, str) else context.org_id
                    lead_uuid = _uuid.UUID(lead_id)
                    row = (await context.db_session.execute(
                        select(Lead).where(Lead.id == lead_uuid, Lead.org_id == org_uuid)
                    )).scalar_one_or_none()
                    if row:
                        phone = row.phone
                        lead_name = row.name
                except Exception:
                    pass
            if not phone:
                return {
                    "status": "no_phone",
                    "reason": (
                        f"Lead '{lead_name or lead_id}' has no phone number stored. "
                        "Ask the user to provide the WhatsApp number (+27...) and use send_whatsapp_direct."
                    ),
                }
            return await self._do_send(phone, args.get("message", ""), args.get("session", ""), lead_name)

        if tool_name == "send_whatsapp_direct":
            phone = (args.get("phone") or "").strip()
            if not phone:
                return {"status": "error", "reason": "phone is required"}
            return await self._do_send(phone, args.get("message", ""), args.get("session", ""))

        if tool_name == "run_safe_batch":
            from app.config import settings
            import httpx
            leads = args.get("lead_ids", [])
            if not leads:
                return {"status": "error", "reason": "No lead_ids provided"}
            session = args.get("session", "")
            message = args.get("message", "")
            url = settings.wa_bot_url.rstrip("/")
            sent, failed, skipped = 0, 0, 0
            if context and context.db_session and context.org_id:
                from sqlalchemy import select
                from app.database.models import Lead
                import uuid as _uuid
                org_uuid = _uuid.UUID(context.org_id) if isinstance(context.org_id, str) else context.org_id
                for lid in leads[:10]:
                    try:
                        row = (await context.db_session.execute(
                            select(Lead).where(Lead.id == _uuid.UUID(lid), Lead.org_id == org_uuid)
                        )).scalar_one_or_none()
                        if not row or not row.phone:
                            skipped += 1
                            continue
                        r = await self._do_send(row.phone, message, session, row.name)
                        if r.get("status") == "sent":
                            sent += 1
                        else:
                            failed += 1
                    except Exception:
                        failed += 1
            return {"status": "complete", "sent": sent, "skipped": skipped, "failed": failed}

        return {"status": "not_implemented", "tool": tool_name}

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        """Convert local SA numbers to international format (+27...)."""
        digits = "".join(c for c in phone if c.isdigit())
        if digits.startswith("0") and len(digits) == 10:
            digits = "27" + digits[1:]
        if not digits.startswith("+"):
            digits = "+" + digits
        return digits

    async def _do_send(self, phone: str, message: str, session: str = "", name: str = "") -> dict:
        from app.config import settings
        import httpx
        phone = self._normalize_phone(phone)
        url = settings.wa_bot_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                # Check connection first so we never lie about sending
                params = {"session": session} if session else {}
                status_resp = await client.get(f"{url}/status", params=params, timeout=5.0)
                status_data = status_resp.json()
                if "sessions" in status_data:
                    connected = any(s.get("connected") for s in status_data.get("sessions", []))
                else:
                    connected = status_data.get("connected", False)
                if not connected:
                    return {
                        "status": "not_sent",
                        "reason": "WhatsApp is not connected — scan the QR code on the Integrations page first.",
                        "to": phone,
                    }

                payload: dict = {"to": phone, "message": message, "session": session or "default"}
                resp = await client.post(f"{url}/api/send", json=payload)
                data = resp.json()

                # Be honest: only report "sent" if the wa-bot confirmed it
                wa_status = data.get("status") or data.get("result") or ""
                error_msg = data.get("error") or data.get("message") or ""
                actually_sent = wa_status in ("sent", "success", "ok", "queued") or resp.status_code < 300

                return {
                    "status": "sent" if actually_sent else "failed",
                    "to": phone,
                    **({"name": name} if name else {}),
                    "wa_response": wa_status or str(data),
                    **({"error": error_msg} if error_msg and not actually_sent else {}),
                }
        except httpx.RequestError as e:
            return {
                "status": "failed",
                "reason": f"WhatsApp bridge unreachable at {url}.",
                "error": str(e),
                "to": phone,
            }
