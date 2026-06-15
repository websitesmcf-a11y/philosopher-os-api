import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.database.models import Conversation, Message
from app.schemas.message import SendMessageRequest


class MessageService:
    def __init__(self, db: AsyncSession, org_id: str = ""):
        self.db = db
        self.org_id = org_id

    async def list_conversations(self, page: int = 1, page_size: int = 20, channel: str = None, lead_id: str = None):
        query = select(Conversation)
        if self.org_id:
            query = query.where(Conversation.org_id == uuid.UUID(self.org_id))
        if channel:
            query = query.where(Conversation.channel == channel)
        if lead_id:
            query = query.where(Conversation.lead_id == lead_id)
        count_q = select(func.count()).select_from(query.subquery())
        total = (await self.db.execute(count_q)).scalar() or 0

        # Correlated subquery: latest message body per conversation
        latest_msg_subq = (
            select(Message.body)
            .where(Message.conversation_id == Conversation.id)
            .order_by(Message.created_at.desc())
            .limit(1)
            .correlate(Conversation)
            .scalar_subquery()
        )

        query = query.add_columns(latest_msg_subq.label("last_message"))
        query = query.order_by(Conversation.last_message_at.desc().nullslast()).offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        rows = result.all()

        items = []
        for c, last_msg in rows:
            resp = self._conv_response(c)
            resp["last_message"] = last_msg
            items.append(resp)

        return {"items": items, "total": total, "page": page, "page_size": page_size}

    async def get_conversation(self, conversation_id: str):
        query = select(Conversation).where(Conversation.id == conversation_id)
        if self.org_id:
            query = query.where(Conversation.org_id == uuid.UUID(self.org_id))
        result = await self.db.execute(query)
        conv = result.scalar_one_or_none()
        if not conv:
            from app.core.errors import NotFoundError
            raise NotFoundError("Conversation not found")

        msg_result = await self.db.execute(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)
        )
        messages = msg_result.scalars().all()

        return {
            "conversation": self._conv_response(conv),
            "messages": [self._msg_response(m) for m in messages],
        }

    async def send_message(self, conversation_id: str, data: SendMessageRequest):
        msg = Message(
            id=uuid.uuid4(),
            conversation_id=uuid.UUID(conversation_id),
            sender_type="user",
            direction="out",
            body=data.body,
            media_url=data.media_url,
        )
        self.db.add(msg)
        await self.db.flush()

        # Try to send via integration channel — recipient comes from the lead record
        try:
            conv_result = await self.db.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conv = conv_result.scalar_one_or_none()
            lead = None
            if conv and conv.lead_id:
                from app.database.models import Lead
                lead_result = await self.db.execute(select(Lead).where(Lead.id == conv.lead_id))
                lead = lead_result.scalar_one_or_none()
            if conv and conv.channel == "whatsapp" and lead and lead.phone:
                from app.integrations.whatsapp import whatsapp
                await whatsapp.send_message(lead.phone, data.body)
            elif conv and conv.channel == "email" and lead and lead.email:
                from app.integrations.email import email_client
                await email_client.send_email(to=lead.email, subject="Message from Socrates AI", text=data.body)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to send outbound message via integration: {e}")

        return self._msg_response(msg)

    def _conv_response(self, conv: Conversation):
        return {
            "id": str(conv.id),
            "org_id": str(conv.org_id),
            "lead_id": str(conv.lead_id) if conv.lead_id else None,
            "client_id": str(conv.client_id) if conv.client_id else None,
            "channel": conv.channel,
            "status": conv.status,
            "extra_metadata": conv.extra_metadata,
            "last_message_at": conv.last_message_at,
            "created_at": conv.created_at,
            "updated_at": conv.updated_at,
        }

    def _msg_response(self, msg: Message):
        return {
            "id": str(msg.id),
            "conversation_id": str(msg.conversation_id),
            "sender_type": msg.sender_type,
            "sender_id": msg.sender_id,
            "direction": msg.direction,
            "body": msg.body,
            "media_url": msg.media_url or [],
            "created_at": msg.created_at,
        }
