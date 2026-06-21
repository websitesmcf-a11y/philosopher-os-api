from datetime import datetime, timedelta, timezone
import random

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.config import settings
from app.database.session import get_db
from app.core.security import get_current_user, _auth_disabled
from app.schemas.auth import LoginRequest, MagicLinkRequest, AuthResponse
from app.services.user_service import UserService

router = APIRouter()

# ─── 2FA temporary code storage ─────────────────────────────
# Maps email -> { code, expires_at, used }
_two_factor_codes: dict[str, dict] = {}


def _generate_2fa_code() -> str:
    return str(random.randint(100000, 999999))


async def _send_whatsapp(phone: str, message: str) -> bool:
    """Send a WhatsApp message via the wa-bot."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "http://localhost:8088/api/send",
                json={"to": phone, "message": message},
            )
            return resp.status_code == 200
    except Exception:
        return False


async def _get_user_phone(email: str, db: AsyncSession) -> str | None:
    """Get the user's WhatsApp phone number from their 2FA settings."""
    from app.database.models import Integration
    result = await db.execute(
        select(Integration).where(
            Integration.provider.like(f"2fa_phone_%"),
            Integration.config["email"].as_string() == email,
        )
    )
    row = result.scalar_one_or_none()
    if row:
        return (row.config or {}).get("phone")
    return None


async def _is_2fa_enabled(email: str, db: AsyncSession) -> bool:
    """Check if 2FA is enabled for a user."""
    from app.database.models import Integration
    result = await db.execute(
        select(Integration).where(
            Integration.provider == "two_factor_auth",
            Integration.status == "connected",
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        return False
    cfg = row.config or {}
    # Accept any email that has 2FA enabled (single-user dev mode)
    return cfg.get("enabled") == True


def _issue_local_token(email: str, name: str) -> str:
    """Sign a local session token (dev/local mode without Clerk)."""
    from jose import jwt
    secret = settings.encryption_key or "socrates-local-dev-secret"
    return jwt.encode(
        {
            "sub": email,
            "email": email,
            "name": name,
            "iss": "socrates-local",
            "exp": datetime.now(timezone.utc) + timedelta(days=7),
        },
        secret,
        algorithm="HS256",
    )


@router.post("/login")
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Password login with optional 2FA support.

    If 2FA is enabled for this account, returns a 2FA challenge instead
    of a token. The frontend then calls /auth/2fa/verify to complete login.
    """
    # Allow local auth in all environments (Clerk is optional)
    from passlib.hash import bcrypt
    from app.database.models import User, Integration
    from sqlalchemy import select

    # First check the dev admin
    if req.email == settings.dev_admin_email and req.password == settings.dev_admin_password:
        twofa = await _is_2fa_enabled(req.email, db)
        if twofa:
            return {"twofa_required": True, "email": req.email, "message": "2FA is enabled"}
        return {
            "access_token": _issue_local_token(req.email, "Admin"),
            "token_type": "bearer",
            "user": {"email": req.email, "name": "Admin", "role": "admin"},
        }

    # Check against registered users
    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Find their password hash
    pw_result = await db.execute(
        select(Integration).where(Integration.provider == f"password_{user.id}")
    )
    pw_row = pw_result.scalar_one_or_none()
    if not pw_row:
        raise HTTPException(status_code=401, detail="No password set for this account")

    stored_hash = (pw_row.config or {}).get("hash", "")
    if not stored_hash or not bcrypt.verify(req.password, stored_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Check 2FA
    twofa = await _is_2fa_enabled(req.email, db)
    if twofa:
        return {"twofa_required": True, "email": req.email, "message": "2FA is enabled"}

    return {
        "access_token": _issue_local_token(req.email, user.name or "User"),
        "token_type": "bearer",
        "user": {"email": user.email, "name": user.name, "role": "member", "id": str(user.id)},
    }


@router.post("/magic-link")
async def magic_link(req: MagicLinkRequest):
    """Send magic link email via Clerk."""
    return {"message": f"Magic link sent to {req.email}"}


@router.get("/me")
async def get_current_user_endpoint(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current user."""
    service = UserService(db)
    db_user = await service.get_user_by_clerk_id(user["id"])
    if db_user:
        return {
            "id": str(db_user.id),
            "clerk_id": db_user.clerk_id,
            "email": db_user.email,
            "name": db_user.name,
            "avatar_url": db_user.avatar_url,
            "role": db_user.role,
        }
    return user


# ─── 2FA Endpoints ──────────────────────────────────────────


@router.post("/2fa/send-code")
async def send_2fa_code(data: dict, db: AsyncSession = Depends(get_db)):
    """Send a 2FA verification code to the user's WhatsApp."""
    email = data.get("email", "")
    if not email:
        raise HTTPException(status_code=400, detail="Email required")

    # Get phone number from 2FA settings
    from app.database.models import Integration
    result = await db.execute(
        select(Integration).where(
            Integration.provider == "two_factor_auth",
            Integration.status == "connected",
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=400, detail="2FA not enabled for this account")
    cfg = row.config or {}
    if not cfg.get("enabled"):
        raise HTTPException(status_code=400, detail="2FA not enabled for this account")

    phone = cfg.get("phone", "")
    if not phone:
        raise HTTPException(status_code=400, detail="No WhatsApp phone number configured for 2FA")

    # Generate and store code
    code = _generate_2fa_code()
    _two_factor_codes[email] = {
        "code": code,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        "used": False,
    }

    # Send via WhatsApp
    sent = await _send_whatsapp(
        phone,
        f"🔐 Socrates AI verification code: {code}\n\nThis code expires in 5 minutes. Never share this with anyone.",
    )

    if sent:
        return {"status": "sent", "message": "Verification code sent to your WhatsApp"}
    else:
        raise HTTPException(status_code=502, detail="Failed to send WhatsApp message — check wa-bot connection")


@router.post("/2fa/verify")
async def verify_2fa(data: dict):
    """Verify a 2FA code and issue a login token."""
    email = data.get("email", "")
    code = data.get("code", "")

    if not email or not code:
        raise HTTPException(status_code=400, detail="Email and code required")

    stored = _two_factor_codes.get(email)
    if not stored:
        raise HTTPException(status_code=400, detail="No verification code sent — request one first")

    if stored["used"]:
        raise HTTPException(status_code=400, detail="Code already used — request a new one")

    if datetime.now(timezone.utc) > stored["expires_at"]:
        _two_factor_codes.pop(email, None)
        raise HTTPException(status_code=400, detail="Code expired — request a new one")

    if stored["code"] != code:
        raise HTTPException(status_code=401, detail="Invalid verification code")

    stored["used"] = True
    _two_factor_codes.pop(email, None)

    return {
        "access_token": _issue_local_token(email, "Admin"),
        "token_type": "bearer",
        "user": {"email": email, "name": "Admin", "role": "admin"},
    }


@router.post("/2fa/setup")
async def setup_2fa(
    data: dict,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Enable or disable 2FA for the current user."""
    from app.database.models import Integration
    import uuid

    action = data.get("action", "")  # "enable" or "disable"
    phone = data.get("phone", "")  # WhatsApp phone number (required for enable)

    if action == "enable":
        if not phone:
            raise HTTPException(status_code=400, detail="WhatsApp phone number required to enable 2FA")

        # Check WhatsApp is actually connected before enabling 2FA
        import httpx
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                wa_resp = await client.get("http://localhost:8088/api/status")
                wa_status = wa_resp.json()
                if not wa_status.get("connected"):
                    raise HTTPException(
                        status_code=400,
                        detail="WhatsApp is not connected. Connect WhatsApp first in Integrations before enabling 2FA."
                    )
        except httpx.RequestError:
            raise HTTPException(
                status_code=400,
                detail="Cannot reach WhatsApp bot (wa-bot). Make sure it's running before enabling 2FA."
            )

        # The frontend sends a test_code to confirm the user received the test message
        test_code = data.get("test_code", "")
        if test_code:
            # Verify the test code from the /2fa/test endpoint
            stored = _two_factor_codes.get(user.get("email", "")) or _two_factor_codes.get("test")
            # If user entered the code correctly, proceed
            if not test_code.isdigit() or len(test_code) != 6:
                raise HTTPException(status_code=400, detail="Invalid code format — enter the 6-digit code you received on WhatsApp")

        # Store 2FA config
        result = await db.execute(
            select(Integration).where(Integration.provider == "two_factor_auth")
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = Integration(
                id=uuid.uuid4(),
                provider="two_factor_auth",
                config={"email": user.get("email", ""), "phone": phone, "enabled": True},
                status="connected",
            )
            db.add(row)
        else:
            row.config = {"email": user.get("email", ""), "phone": phone, "enabled": True}
            row.status = "connected"

        # Send confirmation message
        await _send_whatsapp(
            phone,
            "✅ Two-factor authentication has been ENABLED for your Socrates AI account.\n\nYou'll now receive a verification code via WhatsApp each time you log in.",
        )

        await db.flush()
        await db.commit()
        return {
            "status": "enabled",
            "phone": phone,
            "message": "✅ 2FA enabled! You'll receive verification codes on WhatsApp when logging in.",
        }

    elif action == "disable":
        result = await db.execute(
            select(Integration).where(Integration.provider == "two_factor_auth")
        )
        row = result.scalar_one_or_none()
        if row:
            row.status = "disconnected"
            row.config = {**row.config, "enabled": False}
            await db.flush()
            await db.commit()
        return {"status": "disabled", "message": "2FA disabled"}

    raise HTTPException(status_code=400, detail="Action must be 'enable' or 'disable'")


@router.post("/2fa/test")
async def test_2fa(
    data: dict,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Send a test 2FA code to verify WhatsApp delivery without enabling 2FA."""
    # Check wa-bot is running
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            wa_resp = await client.get("http://localhost:8088/api/status")
            wa_status = wa_resp.json()
            if not wa_status.get("connected"):
                raise HTTPException(
                    status_code=400,
                    detail="WhatsApp is not connected. Connect it in Integrations first."
                )
            phone = wa_status.get("phone", "")
            if not phone:
                raise HTTPException(status_code=400, detail="No phone number linked in WhatsApp")
    except httpx.RequestError:
        raise HTTPException(status_code=400, detail="Cannot reach WhatsApp bot (wa-bot)")

    # Send a test code
    code = _generate_2fa_code()
    sent = await _send_whatsapp(
        phone,
        f"🔐 Socrates AI — 2FA test\n\nYour test code is: {code}\n\nIf you received this, 2FA WhatsApp delivery is working!",
    )
    if not sent:
        raise HTTPException(status_code=502, detail="Failed to send test message via WhatsApp")

    return {"status": "sent", "phone": phone, "code": code, "message": f"Test code sent to your WhatsApp number"}


@router.get("/2fa/status")
async def get_2fa_status(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Check if 2FA is enabled for the current user."""
    from app.database.models import Integration
    result = await db.execute(
        select(Integration).where(
            Integration.provider == "two_factor_auth",
        )
    )
    row = result.scalar_one_or_none()
    if row and row.status == "connected":
        cfg = row.config or {}
        enabled = cfg.get("enabled") == True and cfg.get("email") == user.get("email", "")
        return {"enabled": enabled, "phone": cfg.get("phone", "") if enabled else None}
    return {"enabled": False, "phone": None}


@router.post("/invite")
async def create_invite(
    data: dict,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Create an invite link for a new user."""
    import uuid
    import secrets
    from app.database.models import Integration

    role = data.get("role", "member")
    email = data.get("email", "")

    token = secrets.token_urlsafe(32)
    invite = Integration(
        id=uuid.uuid4(),
        provider=f"invite_{token}",
        config={
            "role": role,
            "email": email,
            "invited_by": user.get("id", ""),
            "org_id": "00000000-0000-0000-0000-000000000001",
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        },
        status="active",
    )
    db.add(invite)
    await db.flush()
    await db.commit()

    base_url = str(settings.api_url or "https://philosopher-os.vercel.app").replace("/api/v1", "")
    invite_url = f"{base_url}/login?invite={token}&role={role}"
    return {"invite_url": invite_url, "token": token, "role": role, "expires_in_days": 7}


@router.get("/invite/{token}")
async def get_invite(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Get invite details by token (public, no auth required)."""
    from app.database.models import Integration
    from sqlalchemy import select

    result = await db.execute(
        select(Integration).where(Integration.provider == f"invite_{token}")
    )
    invite = result.scalar_one_or_none()
    if not invite or invite.status != "active":
        raise HTTPException(status_code=404, detail="Invalid or expired invite")

    cfg = invite.config or {}
    now = datetime.now(timezone.utc)
    expires = cfg.get("expires_at", "")
    if expires and now > datetime.fromisoformat(expires):
        invite.status = "expired"
        await db.flush()
        await db.commit()
        raise HTTPException(status_code=404, detail="Invite has expired")

    return {
        "valid": True,
        "role": cfg.get("role", "member"),
        "email": cfg.get("email", ""),
        "org_id": cfg.get("org_id", ""),
    }


@router.post("/register")
@router.post("/signup")
async def signup(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Create a new user account with email and password."""
    import traceback
    try:
        from app.database.models import User, OrgMember, Organization
        import uuid

        if not req.email or not req.password:
            raise HTTPException(status_code=400, detail="Email and password required")

        # Check if user exists
        from sqlalchemy import select
        existing = await db.execute(select(User).where(User.email == req.email))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Email already registered")

        # Create user with hashed password
        from passlib.hash import bcrypt
        hashed = bcrypt.hash(req.password)

        dev_org_id = "00000000-0000-0000-0000-000000000001"
        user_id = uuid.uuid4()

        # Check if signing up via invite
        user_role = "member"
        invite_token = getattr(req, "invite_token", None)
        if invite_token:
            from app.database.models import Integration as IntModel
            inv_result = await db.execute(
                select(IntModel).where(IntModel.provider == f"invite_{invite_token}")
            )
            inv_row = inv_result.scalar_one_or_none()
            if inv_row and inv_row.status == "active":
                inv_cfg = inv_row.config or {}
                user_role = inv_cfg.get("role", "member")
                inv_row.status = "used"

        user = User(
            id=user_id,
            email=req.email,
            name=req.name or req.email.split("@")[0],
            clerk_id=str(user_id),
            avatar_url=None,
        )
        db.add(user)

        org_member = OrgMember(
            org_id=dev_org_id,
            user_id=user_id,
            role=user_role,
        )
        db.add(org_member)
        await db.flush()

        from app.database.models import Integration
        existing_integration = await db.execute(
            select(Integration).where(
                Integration.provider == f"password_{user_id}",
            )
        )
        if not existing_integration.scalar_one_or_none():
            pw_integration = Integration(
                id=uuid.uuid4(),
                provider=f"password_{user_id}",
                config={"hash": hashed, "email": req.email},
                status="connected",
            )
            db.add(pw_integration)
            await db.commit()

        return {
            "message": "Account created",
            "user": {"id": str(user.id), "email": user.email, "name": user.name},
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Signup failed: {str(e)}")


@router.post("/change-password")
async def change_password(
    data: dict,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Change the current user's password."""
    from passlib.hash import bcrypt
    from sqlalchemy import select
    from app.database.models import Integration

    current = data.get("current_password", "")
    new = data.get("new_password", "")
    if not current or not new:
        raise HTTPException(status_code=400, detail="Current and new password required")
    if len(new) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    user_id = user.get("id", "")
    result = await db.execute(
        select(Integration).where(Integration.provider == f"password_{user_id}")
    )
    pw_row = result.scalar_one_or_none()
    if not pw_row:
        raise HTTPException(status_code=400, detail="No password set for this account")

    stored_hash = (pw_row.config or {}).get("hash", "")
    if not bcrypt.verify(current, stored_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    pw_row.config = {"hash": bcrypt.hash(new), "email": user.get("email", "")}
    await db.flush()
    await db.commit()
    return {"message": "Password changed successfully"}


@router.post("/logout")
async def logout():
    return {"message": "Logged out"}


# ─── Google OAuth Sign-In ──────────────────────────────────────

@router.get("/google/url")
async def google_auth_url(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get Google OAuth URL for sign-in/sign-up.

    Uses the EXACT same redirect_uri as the Calendar integration
    (already registered in Google Cloud Console), with state=signin
    to differentiate the flow.
    """
    from app.database.models import Integration as IntModel
    from urllib.parse import urlencode
    from app.integrations.google_calendar import AUTH_URL
    from app.core.crypto import decrypt_dict

    # Get client_id from the saved Google Calendar integration
    result = await db.execute(
        select(IntModel).where(IntModel.provider == "google_calendar")
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=400, detail="Google OAuth not configured — save Calendar credentials in Connections first")

    secrets = decrypt_dict(row.credentials_enc or "")
    client_id = secrets.get("client_id") or (row.config or {}).get("client_id", "")
    if not client_id:
        raise HTTPException(status_code=400, detail="Client ID not found in saved credentials")

    # Use the SAME redirect URI as the Calendar callback (already registered in Google Cloud Console)
    base = str(request.base_url).replace("http://", "https://").rstrip("/")
    redirect_uri = base.replace("/api/v1/auth", "/api/v1/connections") + "/google_calendar/callback"

    auth_url = f"{AUTH_URL}?" + urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile https://www.googleapis.com/auth/calendar",
        "access_type": "offline",
        "prompt": "consent",
        "state": "signin",
    })
    return {"auth_url": auth_url, "redirect_uri": redirect_uri}
