from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class SendMessageRequest(BaseModel):
    conversation_id: Optional[str] = None
    lead_id: Optional[str] = None
    client_id: Optional[str] = None
    channel: str = "whatsapp"
    body: str
    media_url: list[str] = []


class ConversationResponse(BaseModel):
    id: str
    org_id: str
    lead_id: Optional[str] = None
    client_id: Optional[str] = None
    channel: str
    status: str
    extra_metadata: Optional[dict] = None
    last_message: Optional[str] = None
    last_message_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    sender_type: str
    sender_id: Optional[str] = None
    direction: str
    body: str
    media_url: list[str] = []
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ConversationDetail(BaseModel):
    conversation: ConversationResponse
    messages: list[MessageResponse]
