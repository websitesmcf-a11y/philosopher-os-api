"""Stilbon — Speed Messenger & Communication Operator (God/Titan)"""
import re
import uuid as _uuid
from typing import Any, AsyncGenerator
from app.agents.base import BaseAgent, AgentContext, AgentActionResult, ToolEvent

_PHONE_RE = re.compile(
    r'\b(\+27[0-9\s\-]{8,12}|27[0-9\s\-]{8,12}|0[6-8][0-9\s\-]{7,11})\b'
)
_SEND_WORDS = frozenset([
    'send', 'message', 'msg', 'whatsapp', 'wa', 'text',
    'tell', 'say', 'drop', 'ping', 'reach', 'notify', 'hit',
    'write', 'chat', 'contact', 'forward',
])
# Words that mean the user wants a lookup, NOT a send
_LOOKUP_ONLY = frozenset(['find', 'search', 'look up', 'lookup', 'exists', 'does', 'who is'])


def _extract_phone(text: str) -> str | None:
    m = _PHONE_RE.search(text)
    if m:
        return re.sub(r'[\s\-]', '', m.group(0))
    return None


def _normalize_phone(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    if digits.startswith("0") and len(digits) == 10:
        digits = "27" + digits[1:]
    if not digits.startswith("+"):
        digits = "+" + digits
    return digits


def _phone_in_history(context) -> str | None:
    if not context:
        return None
    for msg in reversed(context.conversation_history or []):
        p = _extract_phone(msg.get("content", ""))
        if p:
            return p
    return None


class Stilbon(BaseAgent):
    LLM_MODEL = "deepseek-v4-pro"
    LLM_MODEL_FALLBACKS = ["deepseek-v4-flash"]

    # Don't let the LLM do a connection check before every send — _do_send handles that
    EXCLUDED_COMMON_TOOLS = {"check_integration"}

    def __init__(self):
        super().__init__(
            name="stilbon",
            role="Speed Messenger & Communication",
            system_prompt=(
                "You are Stilbon, the swift messenger god. You send WhatsApp messages.\n\n"
                "WHEN THE USER GIVES A PHONE NUMBER:\n"
                "→ Call send_whatsapp_direct immediately with that number and the message.\n"
                "→ No CRM record needed. No lead required. Any number works.\n\n"
                "WHEN THE USER GIVES A NAME BUT NO NUMBER:\n"
                "→ Call find_lead_by_name. If found, use the phone. If not, ask for the number.\n\n"
                "RULES:\n"
                "- NEVER redirect to odysseus or any other agent for direct WhatsApp sends.\n"
                "- NEVER say you need a CRM entry or lead_id to send to a phone number.\n"
                "- Instagram DMs are not possible via API — offer WhatsApp instead.\n"
                "- Report the real result. Never claim sent if the tool returned failed."
            ),
        )

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "name": "send_whatsapp_direct",
                "description": (
                    "Send a WhatsApp message to any phone number. "
                    "No CRM record or lead_id needed. "
                    "Works with any format: 0730150646, 073 015 0646, +27730150646."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "phone": {"type": "string", "description": "Phone number (any SA or international format)"},
                        "message": {"type": "string", "description": "Message text to send"},
                    },
                    "required": ["phone", "message"],
                },
            },
            {
                "name": "send_whatsapp",
                "description": "Send WhatsApp to a CRM lead using their lead_id.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "lead_id": {"type": "string"},
                        "message": {"type": "string"},
                    },
                    "required": ["lead_id", "message"],
                },
            },
            {
                "name": "run_safe_batch",
                "description": "Send WhatsApp messages to a batch of CRM leads (max 10 per call).",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "lead_ids": {"type": "array", "items": {"type": "string"}},
                        "message": {"type": "string"},
                        "delay": {"type": "integer"},
                    },
                    "required": ["lead_ids", "message"],
                },
            },
        ]

    # ── Python fast-path ───────────────────────────────────────────────────────

    async def think_stream(
        self, context: AgentContext, on_stop: callable = None
    ) -> AsyncGenerator[str | ToolEvent, None]:
        user_text = context.user_input
        lower = user_text.lower()
        phone = _extract_phone(user_text)

        # Stilbon is a messaging agent. If the current message contains a phone number
        # and doesn't look like a pure lookup query, treat it as a send request.
        is_lookup_only = any(k in lower for k in _LOOKUP_ONLY)
        if phone and not is_lookup_only:
            async for chunk in self._stream_send(phone, context):
                yield chunk
            return

        # Follow-up message ("do it", "try again", "go ahead") after a conversation
        # that already had a phone number — search history.
        has_send = any(k in lower for k in _SEND_WORDS)
        if has_send and not phone:
            phone = _phone_in_history(context)
            if phone:
                async for chunk in self._stream_send(phone, context):
                    yield chunk
                return

        # No phone found anywhere — let the LLM handle it normally
        async for item in super().think_stream(context, on_stop):
            yield item

    async def _stream_send(
        self, phone_raw: str, context: AgentContext
    ) -> AsyncGenerator[str | ToolEvent, None]:
        """Compose message text then send directly — LLM is only used for prose, not tool selection."""
        normalized = _normalize_phone(phone_raw)
        yield f"Sending WhatsApp to {normalized}...\n\n"

        # Compose the message text (no tools, lightweight)
        try:
            resp = await self._llm_generate(
                system=(
                    "You are Stilbon, AI communication agent for Philosopher OS "
                    "(messaging, outreach, contact management). "
                    "Write ONLY the WhatsApp message text — no preamble, no quotes."
                ),
                messages=[{
                    "role": "user",
                    "content": (
                        f"Write the WhatsApp message based on:\n{context.user_input}\n\n"
                        + (
                            "Recent context:\n"
                            + "\n".join(
                                f"{m['role'].upper()}: {m['content'][:200]}"
                                for m in (context.conversation_history or [])[-4:]
                            )
                            if context.conversation_history else ""
                        )
                    ),
                }],
                tools=None,
                temperature=0.7,
            )
            message_text = (resp.content or "").strip()
        except Exception:
            message_text = (
                "Hi! I'm Stilbon, the AI communication agent for Philosopher OS. "
                "I handle messaging, outreach, and contact management. "
                "This is a test message from the system."
            )

        yield ToolEvent("tool_start", "send_whatsapp_direct",
                        input={"phone": normalized, "message": message_text[:80] + "…"})
        try:
            result = await self._do_send(normalized, message_text)
        except Exception as exc:
            result = {"status": "failed", "error": str(exc), "to": normalized}
        yield ToolEvent("tool_end", "send_whatsapp_direct", result=result, duration_ms=0)

        if result.get("status") == "sent":
            yield f"Sent to **{normalized}**.\n\n> {message_text}"
        else:
            reason = result.get("reason") or result.get("error") or result.get("wa_response") or str(result)
            yield f"Send to {normalized} failed: {reason}\n\nMessage drafted:\n> {message_text}"

    # ── Tool execution ─────────────────────────────────────────────────────────

    async def _execute_tool(self, tool_name: str, args: dict, context: AgentContext = None) -> Any:
        if tool_name == "send_whatsapp_direct":
            phone = (args.get("phone") or "").strip()
            if not phone:
                return {"status": "error", "reason": "phone is required"}
            try:
                return await self._do_send(phone, args.get("message", ""))
            except Exception as e:
                return {"status": "failed", "error": str(e)}

        if tool_name == "send_whatsapp":
            lead_id = args.get("lead_id", "")
            phone = None
            lead_name = None
            if context and context.db_session and context.org_id:
                from sqlalchemy import select
                from app.database.models import Lead
                try:
                    org_uuid = _uuid.UUID(context.org_id) if isinstance(context.org_id, str) else context.org_id
                    row = (await context.db_session.execute(
                        select(Lead).where(Lead.id == _uuid.UUID(lead_id), Lead.org_id == org_uuid)
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
                        f"Lead '{lead_name or lead_id}' has no phone stored. "
                        "Ask for the number and use send_whatsapp_direct instead."
                    ),
                }
            try:
                return await self._do_send(phone, args.get("message", ""), name=lead_name)
            except Exception as e:
                return {"status": "failed", "error": str(e)}

        if tool_name == "run_safe_batch":
            leads = args.get("lead_ids", [])
            if not leads:
                return {"status": "error", "reason": "No lead_ids provided"}
            sent, failed, skipped = 0, 0, 0
            if context and context.db_session and context.org_id:
                from sqlalchemy import select
                from app.database.models import Lead
                try:
                    org_uuid = _uuid.UUID(context.org_id) if isinstance(context.org_id, str) else context.org_id
                    for lid in leads[:10]:
                        try:
                            row = (await context.db_session.execute(
                                select(Lead).where(Lead.id == _uuid.UUID(lid), Lead.org_id == org_uuid)
                            )).scalar_one_or_none()
                            if not row or not row.phone:
                                skipped += 1
                                continue
                            r = await self._do_send(row.phone, args.get("message", ""), name=row.name)
                            if r.get("status") == "sent":
                                sent += 1
                            else:
                                failed += 1
                        except Exception:
                            failed += 1
                except Exception:
                    pass
            return {"status": "complete", "sent": sent, "skipped": skipped, "failed": failed}

        return {"status": "not_implemented", "tool": tool_name}

    # ── WhatsApp sender ────────────────────────────────────────────────────────

    async def _do_send(self, phone: str, message: str, name: str = "") -> dict:
        """Send a WhatsApp message. Raises on network failure; returns dict otherwise."""
        from app.config import settings
        import httpx

        phone = _normalize_phone(phone)
        if not phone or phone == "+":
            return {"status": "error", "reason": "Invalid phone number"}

        url = settings.wa_bot_url.rstrip("/")
        async with httpx.AsyncClient(timeout=20.0) as client:
            # Check if WhatsApp is connected — but don't block the send on parse errors
            try:
                status_resp = await client.get(f"{url}/status", timeout=5.0)
                try:
                    status_data = status_resp.json()
                except Exception:
                    status_data = {}
                if "sessions" in status_data:
                    connected = any(s.get("connected") for s in status_data.get("sessions", []))
                else:
                    connected = status_data.get("connected", True)  # optimistic if unparseable
                if not connected:
                    return {
                        "status": "not_sent",
                        "reason": "WhatsApp not connected — scan QR on Integrations page.",
                        "to": phone,
                    }
            except Exception:
                pass  # Can't reach status endpoint — try sending anyway

            # Send the message
            payload = {"to": phone, "message": message, "session": "default"}
            resp = await client.post(f"{url}/api/send", json=payload)
            try:
                data = resp.json()
            except Exception:
                data = {}

            wa_status = data.get("status") or data.get("result") or ""
            error_msg = data.get("error") or data.get("message") or ""
            actually_sent = wa_status in ("sent", "success", "ok", "queued") or resp.status_code < 300

            return {
                "status": "sent" if actually_sent else "failed",
                "to": phone,
                **({"name": name} if name else {}),
                "wa_response": wa_status or str(resp.status_code),
                **({"error": error_msg} if error_msg and not actually_sent else {}),
            }
