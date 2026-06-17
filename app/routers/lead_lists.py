"""Lead Lists — organize leads into named pools that campaigns can reserve.

When a campaign reserves a lead list, all leads in that list are marked with
the campaign's list ID and are hidden from the general lead pool (visible only
to the campaign owner / admins).
"""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, delete as sa_delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.core.security import get_current_user, get_current_org
from app.database.models import Lead

router = APIRouter()


# ─── In-memory / JSON-backed store (fallback when no dedicated table yet) ──
# Once the lead_lists and lead_list_items tables exist in the DB schema, swap
# these for real SQLAlchemy models.

LEAD_LISTS: dict[str, dict] = {}        # list_id -> {id, org_id, name, description, created_by, lead_count, is_archived, created_at}
LEAD_LIST_ITEMS: dict[str, list[str]] = {}  # list_id -> [lead_id, ...]


def _now():
    return datetime.now(timezone.utc).isoformat()


# ─── CRUD ─────────────────────────────────────────────────────────────────

@router.get("")
async def list_lists(
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Return all lead lists for the current org."""
    items = [ll for ll in LEAD_LISTS.values() if ll["org_id"] == org_id and not ll["is_archived"]]
    return {"items": items, "total": len(items)}


@router.post("")
async def create_list(
    body: dict,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Create a new lead list."""
    list_id = str(uuid4())
    now = _now()
    entry = {
        "id": list_id,
        "org_id": org_id,
        "name": body.get("name", "Untitled List"),
        "description": body.get("description", ""),
        "created_by": user.get("id", ""),
        "lead_count": 0,
        "is_archived": False,
        "locked": False,
        "locked_by": None,
        "locked_at": None,
        "created_at": now,
        "updated_at": now,
    }
    LEAD_LISTS[list_id] = entry
    LEAD_LIST_ITEMS[list_id] = []
    return entry


@router.get("/{list_id}")
async def get_list(
    list_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Get a single lead list with its leads."""
    ll = LEAD_LISTS.get(list_id)
    if not ll or ll["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Lead list not found")
    if ll.get("locked"):
        raise HTTPException(status_code=423, detail=f"Lead list '{ll['name']}' is locked by {ll.get('locked_by', 'unknown')}. Unlock it first to modify.")

    lead_ids = LEAD_LIST_ITEMS.get(list_id, [])
    # Fetch leads from the leads table
    leads = []
    if lead_ids:
        result = await db.execute(select(Lead).where(Lead.id.in_(lead_ids)))
        for row in result.scalars().all():
            leads.append({
                "id": str(row.id),
                "name": row.name,
                "phone": row.phone,
                "email": row.email,
                "company": row.company,
                "industry": row.industry,
                "status": row.status,
                "score": row.score or 0,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            })

    return {**ll, "leads": leads}


@router.delete("/{list_id}")
async def delete_list(
    list_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Delete a lead list (does NOT delete the leads themselves)."""
    ll = LEAD_LISTS.get(list_id)
    if not ll or ll["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Lead list not found")
    if ll.get("locked"):
        raise HTTPException(status_code=423, detail=f"Lead list '{ll['name']}' is locked by {ll.get('locked_by', 'unknown')}. Unlock it first to modify.")
    LEAD_LISTS[list_id]["is_archived"] = True
    return {"deleted": True}


# ─── Lead management within lists ─────────────────────────────────────────

@router.post("/{list_id}/leads")
async def add_leads_to_list(
    list_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Add leads to a list."""
    ll = LEAD_LISTS.get(list_id)
    if not ll or ll["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Lead list not found")
    if ll.get("locked"):
        raise HTTPException(status_code=423, detail=f"Lead list '{ll['name']}' is locked by {ll.get('locked_by', 'unknown')}. Unlock it first to modify.")

    lead_ids: list[str] = body.get("lead_ids", [])
    existing = set(LEAD_LIST_ITEMS.get(list_id, []))
    added = 0
    for lid in lead_ids:
        if lid not in existing:
            existing.add(lid)
            added += 1

    # Also mark leads with the list_id on the Lead row itself
    from sqlalchemy import text as sa_text
    if lead_ids:
        for lid in lead_ids:
            await db.execute(sa_text(
                "UPDATE leads SET list_id = ?, updated_at = ? WHERE id = ?"
            ).bindparams(list_id, _now(), lid))
        await db.commit()

    LEAD_LIST_ITEMS[list_id] = list(existing)
    LEAD_LISTS[list_id]["lead_count"] = len(existing)
    LEAD_LISTS[list_id]["updated_at"] = _now()
    return {"added": added}


@router.delete("/{list_id}/leads/{lead_id}")
async def remove_lead_from_list(
    list_id: str,
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Remove a single lead from a list."""
    ll = LEAD_LISTS.get(list_id)
    if not ll or ll["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Lead list not found")
    if ll.get("locked"):
        raise HTTPException(status_code=423, detail=f"Lead list '{ll['name']}' is locked by {ll.get('locked_by', 'unknown')}. Unlock it first to modify.")

    items = LEAD_LIST_ITEMS.get(list_id, [])
    if lead_id in items:
        items.remove(lead_id)
        # Also clear list_id on the lead
        await db.execute(
            text("UPDATE leads SET list_id = NULL, updated_at = :now WHERE id = :id")
            .bindparams(now=_now(), id=lead_id)
        )
        await db.commit()

    LEAD_LIST_ITEMS[list_id] = items
    LEAD_LISTS[list_id]["lead_count"] = len(items)
    LEAD_LISTS[list_id]["updated_at"] = _now()
    return {"removed": True}


# ─── Campaign reservation ─────────────────────────────────────────────────

@router.post("/{list_id}/reserve")
async def reserve_list_for_campaign(
    list_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Reserve all leads in a list for a campaign — locks them from the general pool.

    After this call, the leads in this list become visible only to the campaign
    owner (and admins). They are hidden from the general lead list for other users.
    """
    ll = LEAD_LISTS.get(list_id)
    if not ll or ll["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Lead list not found")
    if ll.get("locked"):
        raise HTTPException(status_code=423, detail=f"Lead list '{ll['name']}' is locked by {ll.get('locked_by', 'unknown')}. Unlock it first to modify.")

    campaign_id = body.get("campaign_id")
    if not campaign_id:
        raise HTTPException(status_code=400, detail="campaign_id is required")

    lead_ids = LEAD_LIST_ITEMS.get(list_id, [])
    reserved = 0

    # Update campaign with the list reference
    from sqlalchemy import text as sa_text
    await db.execute(sa_text(
        "UPDATE campaigns SET lead_list_id = ? WHERE id = ? AND org_id = ?"
    ).bindparams(list_id, campaign_id, org_id))

    # Mark all leads as reserved (set reservation_id on each lead)
    if lead_ids:
        for lid in lead_ids:
            await db.execute(sa_text(
                "UPDATE leads SET reservation_id = ?, updated_at = ? WHERE id = ?"
            ).bindparams(campaign_id, _now(), lid))
        reserved = len(lead_ids)

    await db.commit()

    return {
        "reserved": reserved,
        "campaign_id": campaign_id,
        "list_id": list_id,
    }


# ─── Locking / Unlocking ────────────────────────────────────────────────────


@router.post("/{list_id}/lock")
async def lock_list(
    list_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Lock a lead list so no leads can be added or removed from it."""
    ll = LEAD_LISTS.get(list_id)
    if not ll or ll["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Lead list not found")
    if ll.get("locked"):
        return {"locked": True, "locked_by": ll.get("locked_by"), "message": f"List is already locked by {ll.get('locked_by', 'unknown')}."}
    ll["locked"] = True
    ll["locked_by"] = user.get("id", "unknown")
    ll["locked_at"] = _now()
    ll["updated_at"] = _now()
    return {"locked": True, "locked_by": ll["locked_by"], "message": f"Lead list '{ll['name']}' is now locked."}


@router.post("/{list_id}/unlock")
async def unlock_list(
    list_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Unlock a lead list so leads can be added or removed again."""
    ll = LEAD_LISTS.get(list_id)
    if not ll or ll["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Lead list not found")
    if not ll.get("locked"):
        return {"locked": False, "message": "List is not locked."}
    ll["locked"] = False
    ll["locked_by"] = None
    ll["locked_at"] = None
    ll["updated_at"] = _now()
    return {"locked": False, "message": f"Lead list '{ll['name']}' is now unlocked."}


# ─── Cleanup: remove leads without contact info ──────────────────────────


@router.post("/{list_id}/cleanup")
async def cleanup_lead_list(
    list_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Remove leads from a list that lack phone numbers and/or email addresses.

    Request body:
      - remove_no_phone (bool): remove leads with no phone number
      - remove_no_email (bool): remove leads with no email address

    Both flags can be combined (removes leads missing either).
    """
    ll = LEAD_LISTS.get(list_id)
    if not ll or ll["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Lead list not found")
    if ll.get("locked"):
        raise HTTPException(status_code=423, detail=f"Lead list '{ll['name']}' is locked by {ll.get('locked_by', 'unknown')}. Unlock it first to modify.")

    remove_no_phone = body.get("remove_no_phone", False)
    remove_no_email = body.get("remove_no_email", False)

    if not remove_no_phone and not remove_no_email:
        return {"removed": 0, "message": "No cleanup criteria specified. Set remove_no_phone and/or remove_no_email."}

    lead_ids = LEAD_LIST_ITEMS.get(list_id, [])
    if not lead_ids:
        return {"removed": 0, "message": "List is empty."}

    # Fetch actual lead rows to check phone/email
    result = await db.execute(select(Lead).where(Lead.id.in_(lead_ids)))
    leads = result.scalars().all()

    conditions = []
    if remove_no_phone:
        conditions.append(lambda l: not l.phone)
    if remove_no_email:
        conditions.append(lambda l: not l.email)

    to_remove = []
    for lead in leads:
        if any(check(lead) for check in conditions):
            to_remove.append(str(lead.id))

    if not to_remove:
        return {"removed": 0, "message": f"No leads match the cleanup criteria in this list ({len(lead_ids)} total)."}

    # Remove from the in-memory list
    items = [lid for lid in lead_ids if lid not in to_remove]
    LEAD_LIST_ITEMS[list_id] = items
    LEAD_LISTS[list_id]["lead_count"] = len(items)
    LEAD_LISTS[list_id]["updated_at"] = _now()

    # Clear list_id on the removed lead rows
    from sqlalchemy import text as sa_text
    for lid in to_remove:
        await db.execute(sa_text(
            "UPDATE leads SET list_id = NULL, updated_at = :now WHERE id = :id"
        ).bindparams(now=_now(), id=lid))
    await db.commit()

    summary = []
    if remove_no_phone:
        summary.append("no phone")
    if remove_no_email:
        summary.append("no email")

    return {
        "removed": len(to_remove),
        "remaining": len(items),
        "total_before": len(lead_ids),
        "criteria": summary,
        "message": f"Removed {len(to_remove)} lead(s) with {', '.join(summary)} from '{ll['name']}'.",
    }
