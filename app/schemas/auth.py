from pydantic import BaseModel
from typing import Optional


class LoginRequest(BaseModel):
    email: str
    password: str
    name: Optional[str] = None
    invite_token: Optional[str] = None
    invite_role: Optional[str] = None


class MagicLinkRequest(BaseModel):
    email: str


class AuthResponse(BaseModel):
    token: str
    user: dict
    org: Optional[dict] = None


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    avatar_url: Optional[str] = None
    role: str
    created_at: Optional[str] = None
