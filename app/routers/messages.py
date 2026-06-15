from fastapi import APIRouter, Depends, Query
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.session import get_db
from app.core.security import get_current_user, get_current_org
from app.services.message_service import MessageService
from app.schemas.message import SendMessageRequest, ConversationResponse, MessageResponse, ConversationDetail

router = APIRouter()


@router.get("/conversations")
async def list_conversations(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    channel: Optional[str] = None,
    lead_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = MessageService(db, org_id=org_id)
    return await service.list_conversations(page=page, page_size=page_size, channel=channel, lead_id=lead_id)


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = MessageService(db, org_id=org_id)
    return await service.get_conversation(conversation_id)


@router.post("/conversations/send", status_code=201)
async def send_new_message(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Start a new conversation on a channel and send the first message."""
    import uuid as _uuid
    from datetime import datetime
    from app.database.models import Conversation, Message

    channel = payload.get("channel", "email")
    content = payload.get("content", "")
    conv = Conversation(
        id=_uuid.uuid4(),
        org_id=org_id,
        channel=channel,
        status="active",
        extra_metadata={"to": payload.get("to", "")},
        last_message_at=datetime.utcnow(),
    )
    db.add(conv)
    await db.flush()
    msg = Message(
        id=_uuid.uuid4(),
        conversation_id=conv.id,
        sender_type="user",
        direction="outbound",
        body=content,
    )
    db.add(msg)
    await db.flush()
    return {
        "conversation_id": str(conv.id),
        "message_id": str(msg.id),
        "channel": channel,
        "status": "created",
    }


@router.post("/conversations/{conversation_id}/send")
async def send_message(
    conversation_id: str,
    data: SendMessageRequest,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = MessageService(db, org_id=org_id)
    return await service.send_message(conversation_id, data)
