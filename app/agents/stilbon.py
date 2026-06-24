"""Stilbon — Speed Messenger & Communication Operator (God/Titan)"""
from typing import Any
from app.agents.base import BaseAgent, AgentContext, AgentActionResult


class Stilbon(BaseAgent):
    LLM_MODEL = "deepseek-v4-pro"
    LLM_MODEL_FALLBACKS = ["deepseek-v4-flash"]

    def __init__(self):
        super().__init__(
            name="stilbon",
            role="Speed Messenger & Communication",
            system_prompt=(
                "You are Stilbon, the swift messenger god. You send WhatsApp messages. You execute immediately — no hesitation, no CRM gatekeeping.\n\n"
                "DECISION TREE — follow this EXACTLY, top to bottom, no exceptions:\n\n"
                "CASE 1 — User provides a PHONE NUMBER (any of: 0730150646 / 073 015 0646 / +27730150646 / 27730150646):\n"
                "→ Call send_whatsapp_direct RIGHT NOW with that number.\n"
                "→ DO NOT check the CRM. DO NOT ask for a lead_id. DO NOT say you cannot create a contact.\n"
                "→ A phone number is the ONLY thing you need. The person does not need to be in the CRM.\n\n"
                "CASE 2 — User provides BOTH a name AND a phone number (e.g. 'message Matthew at 0730150646'):\n"
                "→ Same as Case 1. Use the phone number directly. Call send_whatsapp_direct immediately.\n"
                "→ The name is only for personalizing the message text — it is NOT a CRM lookup requirement.\n"
                "→ NEVER say 'I cannot create a personal contact' — you are SENDING, not creating a contact.\n\n"
                "CASE 3 — User provides a NAME but NO number:\n"
                "→ Call find_lead_by_name. If their phone is found, call send_whatsapp_direct.\n"
                "→ If not found, ask the user: 'What is their WhatsApp number?'\n\n"
                "CASE 4 — User provides a LEAD ID (UUID):\n"
                "→ Call send_whatsapp with that lead_id.\n\n"
                "ABSOLUTE RULES:\n"
                "- YOU OWN ALL DIRECT MESSAGING. NEVER redirect WhatsApp sends to odysseus or any other agent.\n"
                "- redirect_to_agent is ONLY allowed when handing off to phantasos (to draft a message) or to another agent for a NON-messaging subtask.\n"
                "- NEVER say 'our system is built around verified business leads' — that is Heraclitus's domain, not yours.\n"
                "- NEVER ask 'do you want me to create a lead first?' — you are a MESSENGER, not a CRM manager.\n"
                "- NEVER claim you cannot send to a personal number. You can send to ANY WhatsApp number.\n"
                "- INSTAGRAM DMs: technically impossible via the Meta Business API without approved access. "
                "Say this directly: 'Instagram DMs require Meta Business API approval which we don't have configured. "
                "I can send via WhatsApp instead — what's their number?' Then offer WhatsApp.\n"
                "- If the user wants a well-crafted message, call get_help_from phantasos to draft it, then send the result with send_whatsapp_direct.\n"
                "- Report the REAL result from the tool. Never claim 'sent' if the tool returned failed.\n"
                "- ALL money is South African Rand (R). Never use $ or USD."
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
                    "USE THIS whenever the user gives a phone number. "
                    "Sends WhatsApp to any number — no CRM entry, no lead_id needed. "
                    "Accepts any format: 0730150646, 073 015 0646, +27730150646."
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

    # ── Code-level guardrails ──────────────────────────────────────────────────

    @staticmethod
    def _extract_phone(text: str) -> str | None:
        """Return the first SA-format phone number found in text, or None."""
        import re
        m = re.search(r'\b(\+27[0-9\s\-]{8,12}|27[0-9\s\-]{8,12}|0[6-8][0-9\s\-]{7,11})\b', text)
        if m:
            return re.sub(r'[\s\-]', '', m.group(0))
        return None

    async def _dispatch_tool(self, tool_name: str, args: dict, context: AgentContext = None) -> Any:
        """
        Code-level override: if the LLM tries to redirect a direct-send request
        to Odysseus (ignoring our instructions), block it and return a strong
        correction so the next round uses send_whatsapp_direct instead.
        """
        if tool_name in ("redirect_to_agent", "get_help_from") and args.get("agent") in (
            "odysseus", "heraclitus", "aristotle"
        ):
            user_text = getattr(context, "user_input", "") if context else ""
            phone = self._extract_phone(user_text)
            if phone:
                return {
                    "status": "blocked_by_stilbon",
                    "correction": (
                        f"WRONG TOOL. You attempted to redirect a direct-send to another agent, "
                        f"but that is forbidden. "
                        f"Phone number detected: {phone}. "
                        f"Call send_whatsapp_direct NOW with phone='{phone}' and the message text. "
                        f"No CRM entry is required. No redirect. Just call send_whatsapp_direct."
                    ),
                }
        return await super()._dispatch_tool(tool_name, args, context)

    async def _build_messages(self, context: AgentContext) -> tuple[list[dict], str]:
        """Inject a code-level directive when a phone number is in the input."""
        messages, context_str = await super()._build_messages(context)
        phone = self._extract_phone(context.user_input)
        if phone and messages:
            # Prepend an undeniable directive to the user turn
            last = messages[-1]
            messages[-1] = {
                **last,
                "content": (
                    f"[SYSTEM DIRECTIVE — PHONE NUMBER DETECTED: {phone}]\n"
                    f"You MUST call send_whatsapp_direct with phone='{phone}'.\n"
                    f"DO NOT call redirect_to_agent for any reason related to this number.\n"
                    f"DO NOT say you need a CRM entry. send_whatsapp_direct works without CRM.\n\n"
                ) + last.get("content", ""),
            }
        return messages, context_str

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
