"""Stilbon — Speed Messenger & Communication Operator (God/Titan)"""
import re
import uuid as _uuid
from typing import Any, AsyncGenerator
from app.agents.base import BaseAgent, AgentContext, AgentActionResult, ToolEvent

_PHONE_RE = re.compile(
    r'\b(\+27[0-9\s\-]{8,12}|27[0-9\s\-]{8,12}|0[6-8][0-9\s\-]{7,11})\b'
)


def _extract_phone(text: str) -> str | None:
    if not text:
        return None
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


def _ctx_phone(context) -> str | None:
    """Find a phone number in the current message or conversation history."""
    if not context:
        return None
    p = _extract_phone(getattr(context, "user_input", "") or "")
    if p:
        return p
    for msg in reversed(getattr(context, "conversation_history", None) or []):
        p = _extract_phone(msg.get("content", ""))
        if p:
            return p
    return None


class Stilbon(BaseAgent):
    LLM_MODEL = "deepseek-v4-pro"
    LLM_MODEL_FALLBACKS = ["deepseek-v4-flash"]

    # Prevent the LLM from doing a connection check before every send
    EXCLUDED_COMMON_TOOLS = {"check_integration"}

    def __init__(self):
        super().__init__(
            name="stilbon",
            role="Speed Messenger & Communication",
            system_prompt=(
                "You are Stilbon, the swift messenger god. You send WhatsApp messages.\n\n"
                "WHEN THE USER GIVES A PHONE NUMBER:\n"
                "→ Call send_whatsapp_direct IMMEDIATELY with that number.\n"
                "→ No CRM check. No lead required. Just send.\n\n"
                "WHEN THE USER GIVES A NAME BUT NO NUMBER:\n"
                "→ Call find_lead_by_name. Use the phone that comes back.\n\n"
                "ABSOLUTE RULES:\n"
                "- NEVER redirect to odysseus or any other agent for a direct send.\n"
                "- NEVER say 'I need a lead record' when a phone number is available.\n"
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
                    "Send a WhatsApp message to ANY phone number. "
                    "No CRM record needed, no lead_id needed. "
                    "Works with 0730150646, 073 015 0646, +27730150646."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "phone": {"type": "string"},
                        "message": {"type": "string"},
                    },
                    "required": ["phone", "message"],
                },
            },
            {
                "name": "find_lead_by_name",
                "description": "Look up a contact by name. Returns their phone number so you can send to them.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                    },
                    "required": ["name"],
                },
            },
            {
                "name": "send_whatsapp",
                "description": "Send WhatsApp to a CRM lead by their lead_id.",
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

    # ── Layer 1: Python fast-path in think_stream (streaming) ──────────────────

    async def think_stream(
        self, context: AgentContext, on_stop: callable = None
    ) -> AsyncGenerator[str | ToolEvent, None]:
        phone = _extract_phone(context.user_input)
        if phone:
            async for chunk in self._direct_send(phone, context):
                yield chunk
            return
        # No phone in current message — let LLM handle (name lookup etc.)
        async for item in super().think_stream(context, on_stop):
            yield item

    # ── Layer 2: Python fast-path in run() (non-streaming) ─────────────────────

    async def run(self, context: AgentContext) -> AgentActionResult:
        phone = _extract_phone(context.user_input)
        if phone:
            normalized = _normalize_phone(phone)
            try:
                msg_resp = await self._llm_generate(
                    system="You are Stilbon. Write ONLY the WhatsApp message text. No preamble.",
                    messages=[{"role": "user", "content": context.user_input}],
                    tools=None,
                    temperature=0.7,
                )
                message_text = (msg_resp.content or "").strip()
            except Exception:
                message_text = (
                    "Hi! I'm Stilbon, AI communication agent for Philosopher OS. "
                    "I handle messaging, outreach, and contact management. Test message."
                )
            try:
                result = await self._do_send(normalized, message_text)
            except Exception as exc:
                result = {"status": "failed", "error": str(exc)}
            if result.get("status") == "sent":
                return AgentActionResult(success=True, message=f"Sent to **{normalized}**.\n\n> {message_text}")
            reason = result.get("reason") or result.get("error") or result.get("wa_response") or str(result)
            return AgentActionResult(success=True, message=f"Send to {normalized} failed: {reason}")
        return await super().run(context)

    async def _direct_send(
        self, phone_raw: str, context: AgentContext
    ) -> AsyncGenerator[str | ToolEvent, None]:
        """Compose and send directly — LLM never selects tools."""
        normalized = _normalize_phone(phone_raw)
        yield f"Sending to {normalized}...\n\n"

        try:
            resp = await self._llm_generate(
                system=(
                    "You are Stilbon, AI communication agent for Philosopher OS "
                    "(messaging, outreach, contact management). "
                    "Write ONLY the WhatsApp message text. No preamble. No quotes."
                ),
                messages=[{
                    "role": "user",
                    "content": (
                        f"Write the WhatsApp message based on:\n{context.user_input}"
                        + (
                            "\n\nRecent context:\n" + "\n".join(
                                f"{m['role'].upper()}: {m['content'][:200]}"
                                for m in (context.conversation_history or [])[-4:]
                            ) if context.conversation_history else ""
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
                "I handle messaging, outreach, and contact management. Test message."
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

    # ── Layer 3: Intercept at tool-execution level (LLM path fallback) ─────────

    async def _execute_tool(self, tool_name: str, args: dict, context: AgentContext = None) -> Any:
        if tool_name == "send_whatsapp_direct":
            phone = (args.get("phone") or "").strip() or _ctx_phone(context)
            if not phone:
                return {"status": "error", "reason": "No phone number found"}
            try:
                return await self._do_send(phone, args.get("message", ""))
            except Exception as exc:
                return {"status": "failed", "error": str(exc)}

        if tool_name == "find_lead_by_name":
            # Try actual CRM lookup first
            name = args.get("name", "")
            found_phone = None
            found_name = None
            if context and context.db_session and context.org_id:
                from sqlalchemy import select, or_
                from app.database.models import Lead
                try:
                    org_uuid = _uuid.UUID(context.org_id) if isinstance(context.org_id, str) else context.org_id
                    rows = (await context.db_session.execute(
                        select(Lead).where(
                            Lead.org_id == org_uuid,
                            or_(
                                Lead.name.ilike(f"%{name}%"),
                                Lead.phone.ilike(f"%{name}%"),
                            ),
                        ).limit(1)
                    )).scalars().all()
                    if rows:
                        found_phone = rows[0].phone
                        found_name = rows[0].name
                except Exception:
                    pass

            if found_phone:
                return {"status": "found", "name": found_name, "phone": found_phone,
                        "note": "Use send_whatsapp_direct with this phone number to send."}

            # Not in CRM — but if user gave a phone number, return that so LLM can use it
            ctx_phone = _ctx_phone(context)
            if ctx_phone:
                return {
                    "status": "not_in_crm",
                    "name": name,
                    "phone": _normalize_phone(ctx_phone),
                    "note": (
                        f"'{name}' is not in the CRM, but the user provided phone {_normalize_phone(ctx_phone)}. "
                        "Call send_whatsapp_direct with this phone number now."
                    ),
                }
            return {"status": "not_found", "name": name,
                    "note": "Ask the user for a phone number, then use send_whatsapp_direct."}

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
            # Layer 3 fallback: use phone from user's message if lead has no phone
            if not phone:
                phone = _ctx_phone(context)
            if not phone:
                return {"status": "no_phone",
                        "reason": "No phone found. Ask the user for the number and use send_whatsapp_direct."}
            try:
                return await self._do_send(phone, args.get("message", ""), name=lead_name)
            except Exception as exc:
                return {"status": "failed", "error": str(exc)}

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
        from app.config import settings
        import httpx

        phone = _normalize_phone(phone)
        if not phone or phone == "+":
            return {"status": "error", "reason": "Invalid phone number"}

        url = settings.wa_bot_url.rstrip("/")
        async with httpx.AsyncClient(timeout=20.0) as client:
            # Check connection status — but don't block on parse errors
            try:
                status_resp = await client.get(f"{url}/status", timeout=5.0)
                try:
                    status_data = status_resp.json()
                except Exception:
                    status_data = {}
                if "sessions" in status_data:
                    connected = any(s.get("connected") for s in status_data.get("sessions", []))
                else:
                    connected = status_data.get("connected", True)
                if not connected:
                    return {"status": "not_sent", "to": phone,
                            "reason": "WhatsApp not connected — scan QR on Integrations page."}
            except Exception:
                pass  # Can't reach status — try sending anyway

            payload = {"to": phone, "message": message, "session": "default"}
            resp = await client.post(f"{url}/api/send", json=payload)
            try:
                data = resp.json()
            except Exception:
                data = {}

            wa_status = data.get("status") or data.get("result") or ""
            error_msg = data.get("error") or data.get("message") or ""
            ok = wa_status in ("sent", "success", "ok", "queued") or resp.status_code < 300

            return {
                "status": "sent" if ok else "failed",
                "to": phone,
                **({"name": name} if name else {}),
                "wa_response": wa_status or str(resp.status_code),
                **({"error": error_msg} if error_msg and not ok else {}),
            }
