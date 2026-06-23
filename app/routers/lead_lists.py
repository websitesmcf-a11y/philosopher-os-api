"""Lead Lists — organize leads into named pools that campaigns can reserve.

When a campaign reserves a lead list, all leads in that list are marked with
the campaign's list ID and are hidden from the general lead pool (visible only
to the campaign owner / admins).

Persisted in the `lead_lists` table (was previously in-memory only).
"""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, update, delete as sa_delete, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.core.security import get_current_user, get_current_org
from app.database.models import Lead, LeadList

router = APIRouter()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _to_dict(ll: LeadList) -> dict:
    """Convert a LeadList ORM row to a plain dict matching the old in-memory shape
    so both the API responses and agent code that reads LEAD_LISTS continue to work."""
    return {
        "id": str(ll.id),
        "org_id": str(ll.org_id),
        "name": ll.name,
        "description": ll.description or "",
        "created_by": str(ll.created_by) if ll.created_by else "",
        "lead_count": ll.lead_count or 0,
        "is_archived": ll.is_archived or False,
        "locked": ll.locked or False,
        "locked_by": ll.locked_by or None,
        "locked_at": ll.locked_at.isoformat() if ll.locked_at else None,
        "created_at": ll.created_at.isoformat() if ll.created_at else None,
        "updated_at": ll.updated_at.isoformat() if ll.updated_at else None,
    }


async def get_lead_list(db: AsyncSession, list_id: str, org_id: str) -> LeadList | None:
    """Fetch a single lead list by ID + org. Returns None if not found."""
    import uuid
    try:
        lid = uuid.UUID(list_id)
    except ValueError:
        return None
    result = await db.execute(
        select(LeadList).where(LeadList.id == lid, LeadList.org_id == uuid.UUID(org_id))
    )
    return result.scalar_one_or_none()


async def get_list_lead_ids(db: AsyncSession, list_id: str) -> list[str]:
    """Fetch all lead IDs that belong to a lead list (using Lead.list_id)."""
    import uuid
    try:
        lid = uuid.UUID(list_id)
    except ValueError:
        return []
    result = await db.execute(
        select(Lead.id).where(Lead.list_id == lid)
    )
    return [str(row[0]) for row in result.all()]


# ─── CRUD ─────────────────────────────────────────────────────────────────


@router.get("")
async def list_lists(
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Return all lead lists for the current org."""
    import uuid
    result = await db.execute(
        select(LeadList).where(
            LeadList.org_id == uuid.UUID(org_id),
            LeadList.is_archived == False,
        ).order_by(LeadList.created_at.desc())
    )
    items = [_to_dict(row) for row in result.scalars().all()]
    return {"items": items, "total": len(items)}


@router.post("")
async def create_list(
    body: dict,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Create a new lead list."""
    import uuid
    ll = LeadList(
        id=uuid.uuid4(),
        org_id=uuid.UUID(org_id),
        name=body.get("name", "Untitled List"),
        description=body.get("description", ""),
        created_by=uuid.UUID(user.get("id", "00000000-0000-0000-0000-000000000000")),
        lead_count=0,
        is_archived=False,
        locked=False,
    )
    db.add(ll)
    await db.flush()
    return _to_dict(ll)


@router.get("/{list_id}")
async def get_list(
    list_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Get a single lead list with its leads."""
    ll = await get_lead_list(db, list_id, org_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")
    if ll.locked:
        raise HTTPException(status_code=423, detail=f"Lead list '{ll.name}' is locked by {ll.locked_by}. Unlock it first to modify.")

    lead_ids = await get_list_lead_ids(db, list_id)
    leads = []
    if lead_ids:
        import uuid
        uuids = []
        for lid in lead_ids:
            try:
                uuids.append(uuid.UUID(lid))
            except ValueError:
                pass
        if uuids:
            result = await db.execute(select(Lead).where(Lead.id.in_(uuids)))
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

    result_dict = _to_dict(ll)
    result_dict["leads"] = leads
    return result_dict


@router.delete("/{list_id}")
async def delete_list(
    list_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Archive a lead list (does NOT delete the leads themselves)."""
    ll = await get_lead_list(db, list_id, org_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")
    if ll.locked:
        raise HTTPException(status_code=423, detail=f"Lead list '{ll.name}' is locked by {ll.locked_by}. Unlock it first to modify.")
    ll.is_archived = True
    await db.flush()
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
    ll = await get_lead_list(db, list_id, org_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")
    if ll.locked:
        raise HTTPException(status_code=423, detail=f"Lead list '{ll.name}' is locked by {ll.locked_by}. Unlock it first to modify.")

    import uuid
    lead_ids: list[str] = body.get("lead_ids", [])
    list_uuid = uuid.UUID(list_id)
    added = 0
    for lid in lead_ids:
        try:
            lead_uuid = uuid.UUID(lid)
            result = await db.execute(
                select(Lead).where(Lead.id == lead_uuid)
            )
            lead = result.scalar_one_or_none()
            if lead and lead.list_id is None:
                lead.list_id = list_uuid
                added += 1
        except ValueError:
            continue

    # Update lead count
    current_ids = await get_list_lead_ids(db, list_id)
    ll.lead_count = len(current_ids)
    await db.flush()
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
    ll = await get_lead_list(db, list_id, org_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")
    if ll.locked:
        raise HTTPException(status_code=423, detail=f"Lead list '{ll.name}' is locked by {ll.locked_by}. Unlock it first to modify.")

    import uuid
    try:
        lead_uuid = uuid.UUID(lead_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid lead ID")

    await db.execute(
        update(Lead).where(Lead.id == lead_uuid).values(list_id=None, updated_at=datetime.now(timezone.utc))
    )
    ll.lead_count = max(0, (ll.lead_count or 1) - 1)
    await db.flush()
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
    """Reserve all leads in a list for a campaign — locks them from the general pool."""
    ll = await get_lead_list(db, list_id, org_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")
    if ll.locked:
        raise HTTPException(status_code=423, detail=f"Lead list '{ll.name}' is locked by {ll.locked_by}. Unlock it first to modify.")

    campaign_id = body.get("campaign_id")
    if not campaign_id:
        raise HTTPException(status_code=400, detail="campaign_id is required")

    import uuid
    campaign_uuid = uuid.UUID(campaign_id)
    lead_ids = await get_list_lead_ids(db, list_id)

    # Update campaign with the list reference
    await db.execute(sa_text(
        "UPDATE campaigns SET lead_list_id = :list_id WHERE id = :cid AND org_id = :oid"
    ).bindparams(list_id=list_id, cid=campaign_id, oid=org_id))

    # Mark leads as reserved
    reserved = 0
    if lead_ids:
        for lid in lead_ids:
            await db.execute(sa_text(
                "UPDATE leads SET reservation_id = :cid, updated_at = :now WHERE id = :id"
            ).bindparams(cid=campaign_id, now=_now(), id=lid))
        reserved = len(lead_ids)

    await db.flush()
    return {"reserved": reserved, "campaign_id": campaign_id, "list_id": list_id}


# ─── Locking / Unlocking ────────────────────────────────────────────────────


@router.post("/{list_id}/lock")
async def lock_list(
    list_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Lock a lead list so no leads can be added or removed from it."""
    ll = await get_lead_list(db, list_id, org_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")
    if ll.locked:
        return {"locked": True, "locked_by": ll.locked_by, "message": f"List is already locked by {ll.locked_by}."}
    ll.locked = True
    ll.locked_by = user.get("id", "unknown")
    ll.locked_at = datetime.now(timezone.utc)
    await db.flush()
    return {"locked": True, "locked_by": ll.locked_by, "message": f"Lead list '{ll.name}' is now locked."}


@router.post("/{list_id}/unlock")
async def unlock_list(
    list_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Unlock a lead list so leads can be added or removed again."""
    ll = await get_lead_list(db, list_id, org_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")
    if not ll.locked:
        return {"locked": False, "message": "List is not locked."}
    ll.locked = False
    ll.locked_by = None
    ll.locked_at = None
    await db.flush()
    return {"locked": False, "message": f"Lead list '{ll.name}' is now unlocked."}


# ─── Cleanup ──────────────────────────────────────────────────────────


@router.post("/{list_id}/cleanup")
async def cleanup_lead_list(
    list_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Remove leads from a list that lack phone numbers and/or email addresses."""
    ll = await get_lead_list(db, list_id, org_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")
    if ll.locked:
        raise HTTPException(status_code=423, detail=f"Lead list '{ll.name}' is locked by {ll.locked_by}. Unlock it first to modify.")

    remove_no_phone = body.get("remove_no_phone", False)
    remove_no_email = body.get("remove_no_email", False)
    if not remove_no_phone and not remove_no_email:
        return {"removed": 0, "message": "No cleanup criteria specified."}

    import uuid
    list_uuid = uuid.UUID(list_id)
    result = await db.execute(select(Lead).where(Lead.list_id == list_uuid))
    leads = result.scalars().all()

    to_remove = []
    for lead in leads:
        if (remove_no_phone and not lead.phone) or (remove_no_email and not lead.email):
            to_remove.append(lead)

    for lead in to_remove:
        lead.list_id = None

    ll.lead_count = (ll.lead_count or 0) - len(to_remove)
    await db.flush()

    criteria = []
    if remove_no_phone:
        criteria.append("no phone")
    if remove_no_email:
        criteria.append("no email")

    return {
        "removed": len(to_remove),
        "remaining": ll.lead_count or 0,
        "total_before": len(leads),
        "criteria": criteria,
    }


# ─── Phone cleaning ────────────────────────────────────────────────────


@router.post("/{list_id}/clean-phones")
async def clean_lead_phones(
    list_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Analyze all phone numbers in a lead list (dry-run)."""
    ll = await get_lead_list(db, list_id, org_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")

    import uuid
    result = await db.execute(select(Lead).where(Lead.list_id == uuid.UUID(list_id)))
    leads = result.scalars().all()

    from app.services.lead_cleaner import clean_lead_list
    lead_dicts = [{"id": str(l.id), "name": l.name, "phone": l.phone or ""} for l in leads]
    report = clean_lead_list(lead_dicts)

    return {
        "list_id": list_id,
        "list_name": ll.name,
        "total": report["total"],
        "valid": report["valid"],
        "fixed": report["fixed"],
        "invalid": report["invalid"],
        "duplicates_found": report["duplicates_removed"],
        "results": report["results"],
    }


@router.patch("/{list_id}/clean-phones/apply")
async def apply_phone_fixes(
    list_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Apply phone number fixes from a previous analysis."""
    ll = await get_lead_list(db, list_id, org_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")

    import uuid
    auto_fix = body.get("auto_fix", True)
    remove_invalid = body.get("remove_invalid", False)
    deduplicate = body.get("deduplicate", False)

    result = await db.execute(select(Lead).where(Lead.list_id == uuid.UUID(list_id)))
    leads = result.scalars().all()

    from app.services.lead_cleaner import clean_lead_list
    lead_dicts = [{"id": str(l.id), "name": l.name, "phone": l.phone or ""} for l in leads]
    report = clean_lead_list(lead_dicts)

    updated = 0
    removed = 0
    fix_log = []

    for r in report["results"]:
        lead = next((l for l in leads if str(l.id) == r["lead_id"]), None)
        if not lead:
            continue

        if auto_fix and r["confidence"] in ("high", "medium") and r["cleaned_phone"]:
            if r["cleaned_phone"] != (lead.phone or "").strip():
                lead.phone = r["cleaned_phone"]
                updated += 1
                fix_log.append({"lead_id": r["lead_id"], "name": r["name"], "from": r["original_phone"], "to": r["cleaned_phone"]})

        if remove_invalid and r["confidence"] == "invalid" and not r.get("splits"):
            lead.list_id = None
            removed += 1
            fix_log.append({"lead_id": r["lead_id"], "name": r["name"], "action": "removed_invalid", "reason": r["reason"]})

    if deduplicate:
        seen_phones: dict[str, str] = {}
        for r in report["results"]:
            if r["cleaned_phone"] and r["confidence"] != "invalid":
                if r["cleaned_phone"] in seen_phones:
                    dup_lead = next((l for l in leads if str(l.id) == r["lead_id"]), None)
                    if dup_lead:
                        dup_lead.list_id = None
                        removed += 1
                        fix_log.append({"lead_id": r["lead_id"], "name": r["name"], "action": "removed_duplicate", "phone": r["cleaned_phone"]})
                else:
                    seen_phones[r["cleaned_phone"]] = r["lead_id"]

    ll.lead_count = (ll.lead_count or 0) - removed
    await db.flush()

    return {"updated": updated, "removed": removed, "fixes": fix_log}
