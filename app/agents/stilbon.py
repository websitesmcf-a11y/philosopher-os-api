"""Stilbon — Speed Messenger & Communication Operator (God/Titan)"""
import re
import uuid as _uuid
from typing import Any, AsyncGenerator
from app.agents.base import BaseAgent, AgentContext, AgentActionResult, ToolEvent

_PHONE_RE = re.compile(
    r'\b(\+27[0-9\s\-]{8,12}|27[0-9\s\-]{8,12}|0[6-8][0-9\s\-]{7,11})\b'
)
_SEND_WORDS = frozenset([
    'send', 'message', 'msg', 'whatsapp', 'wa', 'text', 'tell', 'say',
    'drop', 'ping', 'contact', 'reach', 'chat', 'notify', 'write', 'hit',
])


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


def _find_phone_in_history(context) -> str | None:
    """Search conversation history for a phone number when current msg has none."""
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

    def __init__(self):
        super().__init__(
            name="stilbon",
            role="Speed Messenger & Communication",
            system_prompt=(
                "You are Stilbon, the swift messenger god. You send WhatsApp messages instantly.\n\n"
                "TOOL PRIORITY ORDER — always try these in order:\n\n"
                "1. create_contact_and_send — USE THIS FIRST when you have a phone number (with or without a name).\n"
                "   It creates a contact AND sends the message in one step. No pre-existing CRM record needed.\n\n"
                "2. send_whatsapp_direct — USE THIS if you only have a phone number and the contact already exists.\n\n"
                "3. send_whatsapp — USE THIS only if you have a lead_id from the CRM.\n\n"
                "4. find_lead_by_name — USE THIS only if the user gave a name but NO phone number.\n\n"
                "ABSOLUTE RULES:\n"
                "- NEVER say you need a CRM entry before sending — create_contact_and_send handles that.\n"
                "- NEVER redirect to odysseus, leonidas, or any other agent for direct sends.\n"
                "- INSTAGRAM DMs are not possible via API. Say so, offer WhatsApp instead.\n"
                "- Report honest results. Never claim sent if the tool returned failed."
            ),
        )

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "name": "create_contact_and_send",
                "description": (
                    "PRIMARY TOOL. Use when you have a phone number. "
                    "Creates a contact entry in the CRM and sends WhatsApp in one step. "
                    "No existing lead record required. Works with any phone number."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "phone": {"type": "string", "description": "Phone number (any format: 0730150646, +27730150646)"},
                        "name": {"type": "string", "description": "Contact name (optional, use 'Unknown' if not given)"},
                        "message": {"type": "string", "description": "WhatsApp message text to send"},
                    },
                    "required": ["phone", "message"],
                },
            },
            {
                "name": "send_whatsapp_direct",
                "description": (
                    "Send WhatsApp to any phone number — no CRM record needed. "
                    "Use create_contact_and_send instead if you want to also save the contact."
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
                "description": "Send messages to a batch of CRM leads (max 10 per call).",
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

    # ── Python fast-path: completely bypasses LLM tool selection ───────────────

    async def _fast_send(
        self, phone_raw: str, context: AgentContext
    ) -> AsyncGenerator[str | ToolEvent, None]:
        """Compose a message via lightweight LLM call, then send directly."""
        normalized = _normalize_phone(phone_raw)
        yield f"Sending WhatsApp to {normalized}...\n\n"

        # Compose message (LLM as text generator only, no tools)
        try:
            compose_resp = await self._llm_generate(
                system=(
                    "You are Stilbon, the AI communication agent for Philosopher OS. "
                    "You handle messaging, outreach, and contact management. "
                    "Write ONLY the WhatsApp message text. No preamble. No quotes."
                ),
                messages=[{
                    "role": "user",
                    "content": (
                        f"Write the WhatsApp message based on:\n{context.user_input}\n\n"
                        "Conversation context (if needed):\n"
                        + "\n".join(
                            f"{m['role'].upper()}: {m['content'][:200]}"
                            for m in (context.conversation_history or [])[-4:]
                        )
                    ),
                }],
                tools=None,
                temperature=0.7,
            )
            message_text = (compose_resp.content or "").strip()
        except Exception:
            message_text = (
                "Hi! I'm Stilbon, the AI communication agent for Philosopher OS. "
                "I handle messaging, outreach, and contact management. This is a test."
            )

        # Save a minimal contact entry so there's a CRM record
        if context and context.db_session and context.org_id:
            try:
                from app.database.models import Lead
                org_uuid = _uuid.UUID(context.org_id) if isinstance(context.org_id, str) else context.org_id
                lead = Lead(
                    id=_uuid.uuid4(),
                    org_id=org_uuid,
                    name="Direct Contact",
                    phone=normalized,
                    source="stilbon_direct_send",
                    status="new",
                    score=50,
                )
                context.db_session.add(lead)
                await context.db_session.flush()
            except Exception:
                pass  # Send even if lead creation fails

        yield ToolEvent("tool_start", "send_whatsapp_direct",
                        input={"phone": normalized, "message": message_text[:80] + "…"})
        result = await self._do_send(normalized, message_text)
        yield ToolEvent("tool_end", "send_whatsapp_direct", result=result, duration_ms=0)

        if result.get("status") == "sent":
            yield f"Sent to **{normalized}**.\n\n> {message_text}"
        else:
            reason = (
                result.get("reason") or result.get("error")
                or result.get("wa_response") or str(result)
            )
            yield f"Send to {normalized} failed: {reason}\n\nMessage drafted:\n> {message_text}"

    async def think_stream(
        self, context: AgentContext, on_stop: callable = None
    ) -> AsyncGenerator[str | ToolEvent, None]:
        # Fast path: phone in current message
        phone = _extract_phone(context.user_input)
        has_send = any(k in context.user_input.lower() for k in _SEND_WORDS)
        if phone and has_send:
            async for chunk in self._fast_send(phone, context):
                yield chunk
            return

        # Fast path: phone in conversation history + follow-up send intent
        if has_send and not phone:
            phone = _find_phone_in_history(context)
            if phone:
                async for chunk in self._fast_send(phone, context):
                    yield chunk
                return

        # Normal LLM path for everything else
        async for item in super().think_stream(context, on_stop):
            yield item

    async def run(self, context: AgentContext) -> AgentActionResult:
        """Override run() so fast-path works on non-streaming requests too."""
        phone = _extract_phone(context.user_input)
        has_send = any(k in context.user_input.lower() for k in _SEND_WORDS)
        if not phone and has_send:
            phone = _find_phone_in_history(context)

        if phone and has_send:
            normalized = _normalize_phone(phone)
            try:
                compose_resp = await self._llm_generate(
                    system=(
                        "You are Stilbon. Write ONLY the WhatsApp message text. "
                        "No preamble. No quotes."
                    ),
                    messages=[{
                        "role": "user",
                        "content": f"Write the message based on:\n{context.user_input}",
                    }],
                    tools=None,
                    temperature=0.7,
                )
                message_text = (compose_resp.content or "").strip()
            except Exception:
                message_text = (
                    "Hi! I'm Stilbon, AI communication agent for Philosopher OS. "
                    "Messaging, outreach, contact management. Test message."
                )

            if context and context.db_session and context.org_id:
                try:
                    from app.database.models import Lead
                    org_uuid = _uuid.UUID(context.org_id) if isinstance(context.org_id, str) else context.org_id
                    context.db_session.add(Lead(
                        id=_uuid.uuid4(), org_id=org_uuid, name="Direct Contact",
                        phone=normalized, source="stilbon_direct_send", status="new", score=50,
                    ))
                    await context.db_session.flush()
                except Exception:
                    pass

            result = await self._do_send(normalized, message_text)
            if result.get("status") == "sent":
                msg = f"Sent to **{normalized}**.\n\n> {message_text}"
            else:
                reason = result.get("reason") or result.get("error") or str(result)
                msg = f"Send to {normalized} failed: {reason}"
            return AgentActionResult(success=True, message=msg)

        return await super().run(context)

    # ── Tool execution ─────────────────────────────────────────────────────────

    async def _dispatch_tool(self, tool_name: str, args: dict, context: AgentContext = None) -> Any:
        """Block redirects when a phone is available; augment not-found results."""
        user_text = getattr(context, "user_input", "") if context else ""
        phone = _extract_phone(user_text) or _find_phone_in_history(context)

        if tool_name in ("redirect_to_agent", "get_help_from") and phone:
            if args.get("agent") not in ("phantasos",):
                return {
                    "status": "blocked",
                    "correction": (
                        f"Redirect blocked — phone '{phone}' is available. "
                        f"Call create_contact_and_send with phone='{phone}' right now. "
                        "No CRM record needed — this tool creates one AND sends."
                    ),
                }

        result = await super()._dispatch_tool(tool_name, args, context)

        if tool_name == "find_lead_by_name" and isinstance(result, dict) and result.get("status") == "not_found" and phone:
            result["NEXT_STEP"] = (
                f"Phone '{phone}' is in the user's message. "
                f"Call create_contact_and_send with phone='{phone}' and the message text. "
                "This creates the contact AND sends in one step."
            )
        return result

    async def _execute_tool(self, tool_name: str, args: dict, context: AgentContext = None) -> Any:
        if tool_name == "create_contact_and_send":
            phone_raw = (args.get("phone") or "").strip()
            name = (args.get("name") or "Direct Contact").strip()
            message = (args.get("message") or "").strip()
            if not phone_raw:
                return {"status": "error", "reason": "phone is required"}
            if not message:
                return {"status": "error", "reason": "message is required"}
            normalized = _normalize_phone(phone_raw)

            # Save minimal lead
            if context and context.db_session and context.org_id:
                try:
                    from app.database.models import Lead
                    org_uuid = _uuid.UUID(context.org_id) if isinstance(context.org_id, str) else context.org_id
                    context.db_session.add(Lead(
                        id=_uuid.uuid4(), org_id=org_uuid, name=name, phone=normalized,
                        source="stilbon_direct", status="new", score=50,
                    ))
                    await context.db_session.flush()
                except Exception:
                    pass
            return await self._do_send(normalized, message)

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
                        "Ask for the number and use create_contact_and_send."
                    ),
                }
            return await self._do_send(phone, args.get("message", ""), args.get("session", ""), lead_name)

        if tool_name == "send_whatsapp_direct":
            phone = (args.get("phone") or "").strip()
            if not phone:
                return {"status": "error", "reason": "phone is required"}
            return await self._do_send(phone, args.get("message", ""), args.get("session", ""))

        if tool_name == "run_safe_batch":
            leads = args.get("lead_ids", [])
            if not leads:
                return {"status": "error", "reason": "No lead_ids provided"}
            sent, failed, skipped = 0, 0, 0
            if context and context.db_session and context.org_id:
                from sqlalchemy import select
                from app.database.models import Lead
                org_uuid = _uuid.UUID(context.org_id) if isinstance(context.org_id, str) else context.org_id
                for lid in leads[:10]:
                    try:
                        row = (await context.db_session.execute(
                            select(Lead).where(Lead.id == _uuid.UUID(lid), Lead.org_id == org_uuid)
                        )).scalar_one_or_none()
                        if not row or not row.phone:
                            skipped += 1
                            continue
                        r = await self._do_send(row.phone, args.get("message", ""), args.get("session", ""), row.name)
                        if r.get("status") == "sent":
                            sent += 1
                        else:
                            failed += 1
                    except Exception:
                        failed += 1
            return {"status": "complete", "sent": sent, "skipped": skipped, "failed": failed}

        return {"status": "not_implemented", "tool": tool_name}

    async def _do_send(self, phone: str, message: str, session: str = "", name: str = "") -> dict:
        from app.config import settings
        import httpx
        phone = _normalize_phone(phone)
        if not phone or phone == "+":
            return {"status": "error", "reason": "Invalid phone number after normalization"}
        url = settings.wa_bot_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
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
                        "reason": "WhatsApp not connected — scan QR on Integrations page.",
                        "to": phone,
                    }
                payload: dict = {"to": phone, "message": message, "session": session or "default"}
                resp = await client.post(f"{url}/api/send", json=payload)
                data = resp.json()
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
