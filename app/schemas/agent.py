from pydantic import BaseModel
from typing import Optional, Any
from datetime import datetime


class ChatRequest(BaseModel):
    message: str
    agent: Optional[str] = "plato"
    conversation_id: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    agent: str
    conversation_id: str
    actions: list[dict] = []


class AgentStatus(BaseModel):
    name: str
    role: str
    status: str  # idle, thinking, acting, error
    last_action: Optional[str] = None
    last_action_at: Optional[datetime] = None
    tasks_completed: int = 0
    tasks_failed: int = 0


class MemoryEntry(BaseModel):
    content: str
    memory_type: str = "insight"
    importance: float = 0.5
    metadata: dict = {}
    org_id: Optional[str] = None
