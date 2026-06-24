"""Facebook / Instagram execution — real Graph API calls with saved connections.

Credentials come from the Connections page (encrypted Integration rows):
- facebook:  secrets.page_access_token + config.page_id  → page posts, Messenger replies
- instagram: secrets.access_token + config.account_id    → media publishing + DMs

Every function needs a db session to load credentials and returns a dict the
agents can relay verbatim ("posted", "not_connected", or the Graph error).
"""
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v21.0"


async def _facebook_creds(db) -> tuple[str, str] | dict:
    from app.services.connection_service import get_provider_credentials

    creds = await get_provider_credentials(db, "facebook")
    if not creds:
        return {
            "status": "not_connected",
            "message": "Facebook is not connected. Connect it on the Connections page "
                       "(needs a Page Access Token with pages_manage_posts and the Page ID).",
        }
    secrets, config = creds
    token = secrets.get("page_access_token", "")
    page_id = config.get("page_id", "")
    if not token or not page_id:
        return {"status": "not_connected", "message": "Facebook connection is missing the page token or page ID."}
    return token, page_id


async def exchange_to_permanent_token(db) -> dict:
    """Exchange the stored short-lived token for a never-expiring Page Access Token.

    Flow:
    1. Short-lived user token  →  long-lived user token (60 days)
    2. Long-lived user token   →  permanent Page Access Token (never expires)

    Returns the updated token info or an error dict.
    """
    from app.config import settings
    from app.services.connection_service import get_provider_credentials, save_provider_credentials

    if not settings.facebook_app_id or not settings.facebook_app_secret:
        return {
            "status": "error",
            "message": (
                "FACEBOOK_APP_ID and FACEBOOK_APP_SECRET must be set as Railway environment variables. "
                "Find them in your Meta Developer App → App Settings → Basic."
            ),
        }

    creds = await get_provider_credentials(db, "facebook")
    if not creds:
        return {"status": "not_connected", "message": "Facebook not connected yet."}
    secrets, config = creds
    current_token = secrets.get("page_access_token", "")
    page_id = config.get("page_id", "")
    if not current_token:
        return {"status": "error", "message": "No token stored to exchange."}

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Step 1: exchange for long-lived user token
        r1 = await client.get(
            f"{GRAPH}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.facebook_app_id,
                "client_secret": settings.facebook_app_secret,
                "fb_exchange_token": current_token,
            },
        )
        d1 = r1.json()
        if "error" in d1:
            return {"status": "error", "message": f"Step 1 failed: {d1['error'].get('message')}"}
        long_lived_token = d1.get("access_token", "")
        if not long_lived_token:
            return {"status": "error", "message": "No long-lived token returned from Meta."}

        # Step 2: get permanent Page Access Token
        r2 = await client.get(
            f"{GRAPH}/me/accounts",
            params={"access_token": long_lived_token},
        )
        d2 = r2.json()
        if "error" in d2:
            return {"status": "error", "message": f"Step 2 failed: {d2['error'].get('message')}"}

        pages = d2.get("data", [])
        # Find the page matching our stored page_id, or just take the first
        page_token = None
        found_page_id = page_id
        for page in pages:
            if not page_id or page.get("id") == page_id:
                page_token = page.get("access_token")
                found_page_id = page.get("id", page_id)
                break
        if not page_token and pages:
            page_token = pages[0].get("access_token")
            found_page_id = pages[0].get("id", page_id)

        if not page_token:
            return {"status": "error", "message": "No Page token found. Make sure the connected account manages a Facebook Page."}

    # Save the permanent token back to DB
    await save_provider_credentials(
        db, "facebook",
        secrets={"page_access_token": page_token},
        config={"page_id": found_page_id, "permanent": True},
    )
    logger.info("Facebook Page token exchanged for permanent token (page %s)", found_page_id)
    return {
        "status": "success",
        "message": "Token upgraded to permanent Page Access Token — it will never expire.",
        "page_id": found_page_id,
    }


async def post_to_page(db, message: str, link: str | None = None) -> dict:
    """Publish a post on the connected Facebook Page."""
    creds = await _facebook_creds(db)
    if isinstance(creds, dict):
        return creds
    token, page_id = creds
    body: dict[str, Any] = {"message": message, "access_token": token}
    if link:
        body["link"] = link
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{GRAPH}/{page_id}/feed", data=body)
            data = resp.json()
            if resp.status_code == 200 and data.get("id"):
                logger.info(f"Facebook post published: {data['id']}")
                return {"status": "posted", "post_id": data["id"]}
            err = data.get("error", {}).get("message", f"HTTP {resp.status_code}")
            return {"status": "error", "message": f"Facebook rejected the post: {err}"}
    except Exception as e:
        logger.error(f"Facebook post failed: {e}")
        return {"status": "error", "message": f"Facebook post failed: {e}"}


async def send_messenger_message(db, recipient_id: str, text: str) -> dict:
    """Reply to a Messenger conversation. recipient_id is the sender's PSID."""
    creds = await _facebook_creds(db)
    if isinstance(creds, dict):
        return creds
    token, _page_id = creds
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{GRAPH}/me/messages",
                params={"access_token": token},
                json={
                    "recipient": {"id": recipient_id},
                    "message": {"text": text},
                    "messaging_type": "RESPONSE",
                },
            )
            data = resp.json()
            if resp.status_code == 200:
                return {"status": "sent", "message_id": data.get("message_id")}
            err = data.get("error", {}).get("message", f"HTTP {resp.status_code}")
            return {"status": "error", "message": f"Messenger send failed: {err}"}
    except Exception as e:
        return {"status": "error", "message": f"Messenger send failed: {e}"}


async def send_instagram_reply(db, recipient_id: str, text: str) -> dict:
    """Reply to an Instagram DM. recipient_id is the sender's Instagram-scoped ID."""
    from app.services.connection_service import get_provider_credentials

    creds = await get_provider_credentials(db, "instagram")
    # Instagram DMs can go through either the instagram or facebook integration token
    if not creds:
        creds_fb = await _facebook_creds(db)
        if isinstance(creds_fb, dict):
            return {"status": "not_connected", "message": "Neither Instagram nor Facebook is connected."}
        token = creds_fb[0]
    else:
        secrets, _ = creds
        token = secrets.get("access_token", "") or secrets.get("page_access_token", "")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{GRAPH}/me/messages",
                params={"access_token": token},
                json={
                    "recipient": {"id": recipient_id},
                    "message": {"text": text},
                    "messaging_type": "RESPONSE",
                },
            )
            data = resp.json()
            if resp.status_code == 200:
                return {"status": "sent", "message_id": data.get("message_id")}
            err = data.get("error", {}).get("message", f"HTTP {resp.status_code}")
            return {"status": "error", "message": f"Instagram reply failed: {err}"}
    except Exception as e:
        return {"status": "error", "message": f"Instagram reply failed: {e}"}


async def post_to_instagram(db, image_url: str, caption: str = "") -> dict:
    """Publish an image post to the connected Instagram Business account."""
    from app.services.connection_service import get_provider_credentials

    creds = await get_provider_credentials(db, "instagram")
    if not creds:
        return {
            "status": "not_connected",
            "message": "Instagram is not connected. Connect it on the Connections page.",
        }
    secrets, config = creds
    token = secrets.get("access_token", "")
    account_id = config.get("account_id", "")
    if not token or not account_id:
        return {"status": "not_connected", "message": "Instagram connection is missing the token or account ID."}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            container = await client.post(
                f"{GRAPH}/{account_id}/media",
                data={"image_url": image_url, "caption": caption, "access_token": token},
            )
            cdata = container.json()
            if container.status_code != 200 or not cdata.get("id"):
                err = cdata.get("error", {}).get("message", f"HTTP {container.status_code}")
                return {"status": "error", "message": f"Instagram container failed: {err}"}
            publish = await client.post(
                f"{GRAPH}/{account_id}/media_publish",
                data={"creation_id": cdata["id"], "access_token": token},
            )
            pdata = publish.json()
            if publish.status_code == 200 and pdata.get("id"):
                return {"status": "posted", "media_id": pdata["id"]}
            err = pdata.get("error", {}).get("message", f"HTTP {publish.status_code}")
            return {"status": "error", "message": f"Instagram publish failed: {err}"}
    except Exception as e:
        return {"status": "error", "message": f"Instagram post failed: {e}"}
