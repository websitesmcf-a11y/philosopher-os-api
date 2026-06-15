import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.agent import ChatRequest, ChatResponse
from app.database.session import get_db, async_session
from app.core.security import get_current_user, get_current_org
from app.database.models import Conversation, Message
from app.services.event_bus import broadcast

logger = logging.getLogger(__name__)
router = APIRouter()


def _history_role(sender_type: str) -> str:
    """Map stored sender types onto LLM message roles."""
    return "assistant" if sender_type in ("agent", "assistant") else "user"


async def _get_conversation(db: AsyncSession, conversation_id: str, org_id: str) -> Conversation | None:
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.org_id == org_id,
        )
    )
    return result.scalar_one_or_none()


async def _get_or_create_conversation(
    db: AsyncSession, org_id: str, agent_name: str, conversation_id: str | None
) -> Conversation:
    if conversation_id:
        conv = await _get_conversation(db, conversation_id, org_id)
        if conv:
            return conv
    conv = Conversation(
        id=uuid.uuid4(),
        org_id=org_id,
        channel="agent",
        status="active",
        extra_metadata={"agent": agent_name},
        last_message_at=datetime.utcnow(),
    )
    db.add(conv)
    await db.flush()
    return conv


async def _load_history(db: AsyncSession, conversation_id) -> list[dict]:
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
        .limit(50)
    )
    return [
        {"role": _history_role(m.sender_type), "content": m.body}
        for m in result.scalars().all()
    ]


async def _save_message(
    db: AsyncSession, conversation: Conversation, sender_type: str, body: str, agent: str | None = None
) -> Message:
    msg = Message(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        sender_type=sender_type,
        sender_id=agent,
        direction="outbound" if sender_type == "agent" else "inbound",
        body=body,
    )
    db.add(msg)
    conversation.last_message_at = datetime.utcnow()
    await db.flush()
    return msg


@router.post("/chat")
async def chat_with_agents(
    req: ChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Send a message to the AI council. The selected agent (if any) handles it;
    otherwise the council routes. Every turn is persisted."""
    council = request.app.state.council

    requested_agent = req.agent if req.agent in council.agents else "plato"
    conv = await _get_or_create_conversation(db, org_id, requested_agent, req.conversation_id)
    history = await _load_history(db, conv.id)

    await _save_message(db, conv, "user", req.message)

    result = await council.process(
        user_input=req.message,
        org_id=org_id,
        db_session=db,
        conversation_history=history or None,
        agent=req.agent,
    )

    reply = result.get("reply") or ""
    agent_used = result.get("agent") or requested_agent
    if reply:
        await _save_message(db, conv, "agent", reply, agent=agent_used)

    return ChatResponse(
        reply=reply,
        agent=agent_used,
        conversation_id=str(conv.id),
        actions=result.get("actions") or [],
    )


@router.post("/chat/stream")
async def chat_stream(
    req: ChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Stream AI council response tokens via Server-Sent Events. Persists both turns."""
    council = request.app.state.council

    requested_agent = req.agent if req.agent in council.agents else "plato"
    conv = await _get_or_create_conversation(db, org_id, requested_agent, req.conversation_id)
    history = await _load_history(db, conv.id)

    await _save_message(db, conv, "user", req.message)
    conv_id = str(conv.id)

    # The request-scoped session is committed and closed as soon as this
    # handler returns the StreamingResponse, so the stream (and the reply
    # persistence at its end) must run on its own session.
    async def event_generator():
        async with async_session() as stream_db:
            stream_conv = await stream_db.get(Conversation, conv.id)

            async def persist_reply(agent_name: str, full_text: str):
                if full_text and stream_conv is not None:
                    await _save_message(stream_db, stream_conv, "agent", full_text, agent=agent_name)

            try:
                async for event in council.process_stream(
                    user_input=req.message,
                    org_id=org_id,
                    db_session=stream_db,
                    conversation_history=history or None,
                    agent=req.agent,
                    conversation_id=conv_id,
                    on_complete=persist_reply,
                ):
                    yield event
                await stream_db.commit()
            except Exception:
                await stream_db.rollback()
                raise

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/chat/conversations")
async def list_agent_conversations(
    agent: str | None = None,
    q: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """List persisted agent-chat conversations, optionally filtered by agent or text."""
    query = (
        select(Conversation)
        .where(Conversation.org_id == org_id, Conversation.channel == "agent")
        .order_by(Conversation.last_message_at.desc())
        .limit(200)
    )
    rows = (await db.execute(query)).scalars().all()

    if agent:
        rows = [c for c in rows if (c.extra_metadata or {}).get("agent") == agent]

    if q:
        like = f"%{q.lower()}%"
        match_result = await db.execute(
            select(Message.conversation_id)
            .where(Message.body.ilike(like))
            .distinct()
        )
        matched_ids = {str(r) for (r,) in match_result.all()}
        rows = [c for c in rows if str(c.id) in matched_ids]

    rows = rows[:limit]

    out = []
    for c in rows:
        last = (await db.execute(
            select(Message).where(Message.conversation_id == c.id)
            .order_by(Message.created_at.desc()).limit(1)
        )).scalar_one_or_none()
        out.append({
            "id": str(c.id),
            "agent": (c.extra_metadata or {}).get("agent", "plato"),
            "last_message": (last.body[:120] if last else ""),
            "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        })
    return {"items": out, "total": len(out)}


@router.get("/chat/conversations/{conversation_id}/messages")
async def get_agent_conversation_messages(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Full message history for one agent conversation."""
    conv = await _get_conversation(db, conversation_id, org_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv.id)
        .order_by(Message.created_at)
    )
    return {
        "conversation_id": str(conv.id),
        "agent": (conv.extra_metadata or {}).get("agent", "plato"),
        "items": [
            {
                "id": str(m.id),
                "role": _history_role(m.sender_type),
                "agent": m.sender_id,
                "content": m.body,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in result.scalars().all()
        ],
    }
