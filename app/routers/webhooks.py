from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database.session import get_db
from app.database.models import Conversation, Message
from app.integrations.whatsapp import whatsapp
from app.config import settings
import hmac
import hashlib
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/clerk")
async def clerk_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Handle Clerk webhooks with svix signature verification."""
    payload = await request.json()
    event_type = payload.get("type", "unknown")
    logger.info(f"Clerk webhook: {event_type}")

    # Svix signature verification
    if settings.clerk_webhook_secret:
        try:
            import svix
            svix_id = request.headers.get("svix-id", "")
            svix_timestamp = request.headers.get("svix-timestamp", "")
            svix_signature = request.headers.get("svix-signature", "")
            body = await request.body()

            wh = svix.Webhook(settings.clerk_webhook_secret)
            wh.verify(body, {
                "svix-id": svix_id,
                "svix-timestamp": svix_timestamp,
                "svix-signature": svix_signature,
            })
        except Exception as e:
            logger.warning(f"Clerk webhook verification failed: {e}")
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # Sync user on user.created / user.updated
    if event_type in ("user.created", "user.updated"):
        data = payload.get("data", {})
        from app.services.user_service import UserService
        from app.schemas.user import UserCreate
        user_service = UserService(db)
        await user_service.upsert_user(UserCreate(
            clerk_id=data.get("id", ""),
            email=data.get("email_addresses", [{}])[0].get("email_address", ""),
            name=f"{data.get('first_name', '')} {data.get('last_name', '')}".strip(),
            avatar_url=data.get("image_url"),
        ))
        logger.info(f"Clerk user synced: {data.get('id', '')}")

    return {"received": True}


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Handle incoming WhatsApp messages from wa_bot."""
    payload = await request.json()
    logger.info(f"WhatsApp webhook received: {payload.get('event', 'unknown')}")

    # Validate webhook secret if configured
    if settings.whatsapp_webhook_secret:
        signature = request.headers.get("X-Webhook-Signature", "")
        body = await request.body()
        expected = hmac.new(
            settings.whatsapp_webhook_secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        if signature != expected:
            logger.warning("Invalid WhatsApp webhook signature")
            raise HTTPException(status_code=401, detail="Invalid signature")

    message_data = payload.get("message", payload)
    from_number = message_data.get("from", "") or message_data.get("sender", "")
    text = message_data.get("text", "") or message_data.get("body", "")

    if from_number and text:
        logger.info(f"Incoming WhatsApp from {from_number}: {text[:100]}")

        # Route to council for processing
        try:
            from app.agents.council import CouncilOrchestrator
            council = CouncilOrchestrator()
            # Find or create conversation by phone number
            result = await db.execute(
                select(Conversation).where(Conversation.extra_metadata["phone"].as_string() == from_number)
            )
            conv = result.scalar_one_or_none()
            conv_id = str(conv.id) if conv else None

            council_result = await council.process(
                user_input=text,
                org_id=None,
                db_session=db,
                conversation_history=None,
            )
            reply = council_result.get("reply", "")

            # Send reply back via WhatsApp
            if reply:
                await whatsapp.send_message(from_number, reply)

        except Exception as e:
            logger.error(f"Failed to process WhatsApp message via council: {e}")

    return {"received": True}


@router.post("/email")
async def email_webhook(request: Request):
    payload = await request.json()
    logger.info(f"Email webhook received")
    return {"received": True}


@router.get("/whatsapp-status")
async def get_whatsapp_status():
    """Check WhatsApp connection status via wa_bot."""
    status = await whatsapp.get_whatsapp_status()
    return status


# ─── WhatsApp Business Cloud API (Meta Official) ──────────────────────────

WHATSAPP_VERIFY_TOKEN = "philosopher_os_verify_2026"

@router.get("/whatsapp-business")
async def whatsapp_business_verify(request: Request):
    """Verify webhook with Facebook/Meta Developers.

    Meta sends a GET with ?hub.mode=subscribe&hub.verify_token=...&hub.challenge=...
    We respond with the challenge value if the token matches.
    """
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        logger.info("WhatsApp Business webhook verified by Meta")
        return int(challenge) if challenge and challenge.isdigit() else challenge
    else:
        logger.warning(f"WhatsApp Business verify failed: mode={mode} token={token}")
        raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/whatsapp-business")
async def whatsapp_business_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Handle incoming messages from WhatsApp Business Cloud API."""
    payload = await request.json()

    # Meta sends: { object: "whatsapp_business_account", entry: [{ changes: [{ value: { messages: [...] } }] }] }
    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    from_number = msg.get("from", "")
                    text = ""
                    # Handle text messages
                    if msg.get("type") == "text":
                        text = msg.get("text", {}).get("body", "")
                    # Handle interactive replies (button/list replies)
                    elif msg.get("type") == "interactive":
                        interactive = msg.get("interactive", {})
                        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
                        text = reply.get("title", "") or reply.get("id", "")

                    if from_number and text:
                        logger.info(f"WhatsApp Business msg from {from_number}: {text[:100]}")

                        # Route to AI council for processing
                        try:
                            from app.agents.council import CouncilOrchestrator
                            council = CouncilOrchestrator()
                            council_result = await council.process(
                                user_input=text,
                                org_id=None,
                                db_session=db,
                                conversation_history=None,
                            )
                            reply = council_result.get("reply", "")

                            if reply:
                                # Queue reply via the WhatsApp Business API
                                from app.integrations.whatsapp import whatsapp
                                await whatsapp.send_message(from_number, reply)
                        except Exception as e:
                            logger.error(f"Failed to process WhatsApp Business message: {e}")
    except Exception as e:
        logger.error(f"Error parsing WhatsApp Business webhook: {e}")

    # Meta expects a 200 OK to acknowledge receipt
    return {"received": True}
