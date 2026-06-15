"""Facebook / Instagram execution — real Graph API calls with saved connections.

Credentials come from the Connections page (encrypted Integration rows):
- facebook:  secrets.page_access_token + config.page_id  → page posts, Messenger replies
- instagram: secrets.access_token + config.account_id    → media publishing

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


async def post_to_page(db, message: str, link: str | None = None) -> dict:
    """Publish a post on the connected Facebook Page. Returns the post id."""
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
    """Send a Messenger message from the Page (recipient must have messaged the page)."""
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
                    "messaging_type": "MESSAGE_TAG",
                    "tag": "ACCOUNT_UPDATE",
                },
            )
            data = resp.json()
            if resp.status_code == 200:
                return {"status": "sent", "message_id": data.get("message_id")}
            err = data.get("error", {}).get("message", f"HTTP {resp.status_code}")
            return {"status": "error", "message": f"Messenger send failed: {err}"}
    except Exception as e:
        return {"status": "error", "message": f"Messenger send failed: {e}"}


async def post_to_instagram(db, image_url: str, caption: str = "") -> dict:
    """Publish an image post to the connected Instagram Business account.

    Instagram's Graph API cannot publish text-only posts — an image URL is required.
    Two-step flow: create a media container, then publish it.
    """
    from app.services.connection_service import get_provider_credentials

    creds = await get_provider_credentials(db, "instagram")
    if not creds:
        return {
            "status": "not_connected",
            "message": "Instagram is not connected. Connect it on the Connections page "
                       "(requires an Instagram Business account linked to a Facebook Page).",
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
