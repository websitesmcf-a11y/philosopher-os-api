"""CRM Cleanup — real, DB-backed data-integrity operations.

Powers the "CRM Cleanup" tool (Erebos). Unlike the chat agent, these
endpoints query the actual `leads` / `campaigns` tables and return concrete
numbers, so the dashboard reflects reality instead of an LLM's guess.

All endpoints are org-scoped via `get_current_org`.
"""

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.core.security import get_current_user, get_current_org
from app.database.models import Lead, Campaign, CampaignLead

router = APIRouter()


# ─── Helpers ─────────────────────────────────────────────────────────────

def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def _dup_key(lead: Lead) -> tuple[str, str] | None:
    """The key used to consider two leads duplicates.

    A lead is a duplicate of another when they share a non-empty phone
    number, OR the same (name + company). Returns None when the lead has
    no usable identifying data.
    """
    phone = _norm(lead.phone)
    if phone:
        return ("phone", phone)
    name = _norm(lead.name)
    company = _norm(lead.company)
    if name and company:
        return ("name_company", f"{name}|{company}")
    return None


def _completeness(lead: Lead) -> int:
    """Higher = more complete. Used to pick which duplicate to keep."""
    score = 0
    for attr in ("phone", "email", "company", "industry", "source", "notes"):
        if _norm(getattr(lead, attr, None)):
            score += 1
    if lead.tags:
        score += 1
    if lead.score:
        score += 1
    if lead.custom_fields:
        score += 1
    # Prefer the lead with the most progressed status as a tiebreaker.
    if _norm(lead.status) not in ("", "new"):
        score += 1
    return score


async def _load_leads(db: AsyncSession, org_id: str) -> list[Lead]:
    result = await db.execute(select(Lead).where(Lead.org_id == org_id))
    return list(result.scalars().all())


def _group_duplicates(leads: list[Lead]) -> list[list[Lead]]:
    """Group leads that share a duplicate key. Only groups of >=2 returned."""
    buckets: dict[tuple[str, str], list[Lead]] = {}
    for lead in leads:
        key = _dup_key(lead)
        if key is None:
            continue
        buckets.setdefault(key, []).append(lead)
    return [grp for grp in buckets.values() if len(grp) > 1]


# ─── Duplicates ──────────────────────────────────────────────────────────

@router.get("/duplicates")
async def find_duplicates(
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Find duplicate leads (matched by phone OR name+company)."""
    leads = await _load_leads(db, org_id)
    groups = _group_duplicates(leads)

    out_groups = []
    duplicate_records = 0
    for grp in groups:
        # Newest/most complete first for display.
        grp_sorted = sorted(grp, key=_completeness, reverse=True)
        key = _dup_key(grp_sorted[0])
        duplicate_records += len(grp) - 1  # how many would be removed
        out_groups.append({
            "match_type": key[0] if key else "unknown",
            "match_value": grp_sorted[0].phone or f"{grp_sorted[0].name} @ {grp_sorted[0].company}",
            "count": len(grp),
            "leads": [
                {
                    "id": str(l.id),
                    "name": l.name,
                    "phone": l.phone,
                    "email": l.email,
                    "company": l.company,
                    "status": l.status,
                    "completeness": _completeness(l),
                }
                for l in grp_sorted
            ],
        })

    # Largest groups first.
    out_groups.sort(key=lambda g: g["count"], reverse=True)
    return {
        "total_leads": len(leads),
        "duplicate_groups": len(out_groups),
        "duplicate_records": duplicate_records,
        "groups": out_groups,
    }


@router.post("/merge-duplicates")
async def merge_duplicates(
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Merge duplicate leads.

    For each duplicate group the most complete lead is kept; the others are
    backfilled into it (filling blank fields) and then deleted. Campaign
    memberships pointing at deleted leads are repointed to the survivor.
    """
    leads = await _load_leads(db, org_id)
    groups = _group_duplicates(leads)

    merged_count = 0
    groups_merged = 0
    fill_attrs = ("phone", "email", "company", "industry", "source", "notes")

    for grp in groups:
        grp_sorted = sorted(grp, key=_completeness, reverse=True)
        survivor = grp_sorted[0]
        losers = grp_sorted[1:]
        if not losers:
            continue
        groups_merged += 1

        for loser in losers:
            # Backfill blank fields on the survivor from the loser.
            for attr in fill_attrs:
                if not _norm(getattr(survivor, attr, None)) and _norm(getattr(loser, attr, None)):
                    setattr(survivor, attr, getattr(loser, attr))
            # Keep the higher score.
            if (loser.score or 0) > (survivor.score or 0):
                survivor.score = loser.score

            # Repoint campaign memberships, avoiding duplicate (campaign, lead) PKs.
            cl_result = await db.execute(
                select(CampaignLead).where(CampaignLead.lead_id == loser.id)
            )
            for cl in cl_result.scalars().all():
                exists = await db.execute(
                    select(CampaignLead).where(
                        CampaignLead.campaign_id == cl.campaign_id,
                        CampaignLead.lead_id == survivor.id,
                    )
                )
                if exists.scalar_one_or_none() is None:
                    cl.lead_id = survivor.id
                else:
                    await db.delete(cl)

            await db.delete(loser)
            merged_count += 1

    await db.commit()
    return {
        "merged": merged_count,
        "groups_merged": groups_merged,
        "remaining_leads": len(leads) - merged_count,
    }


# ─── Audit ───────────────────────────────────────────────────────────────

@router.get("/audit")
async def audit_data_quality(
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Real data-quality audit over the org's leads."""
    base = select(func.count(Lead.id)).where(Lead.org_id == org_id)

    total = (await db.execute(base)).scalar() or 0

    def _blank(col):
        return (col.is_(None)) | (func.trim(col) == "")

    missing_phone = (await db.execute(
        base.where(_blank(Lead.phone))
    )).scalar() or 0
    missing_email = (await db.execute(
        base.where(_blank(Lead.email))
    )).scalar() or 0
    missing_company = (await db.execute(
        base.where(_blank(Lead.company))
    )).scalar() or 0
    no_contact_method = (await db.execute(
        base.where(_blank(Lead.phone) & _blank(Lead.email))
    )).scalar() or 0

    # Duplicates (reuse the in-memory grouping for accuracy).
    leads = await _load_leads(db, org_id)
    groups = _group_duplicates(leads)
    duplicate_records = sum(len(g) - 1 for g in groups)

    # A lead "has an issue" if it is missing any contact method/company or is a
    # redundant duplicate. Health score = 100 - pct of leads with issues.
    leads_with_issues = no_contact_method + duplicate_records
    # Cap so the score stays in [0, 100] even if categories overlap.
    leads_with_issues = min(leads_with_issues, total)
    health_score = 100.0 if total == 0 else round(100.0 * (1 - leads_with_issues / total), 1)

    return {
        "total_leads": total,
        "missing_phone": missing_phone,
        "missing_email": missing_email,
        "missing_company": missing_company,
        "no_contact_method": no_contact_method,
        "duplicate_records": duplicate_records,
        "leads_with_issues": leads_with_issues,
        "health_score": health_score,
    }


# ─── Campaign status cleanup ───────────────────────────────────────────────

@router.post("/fix-campaigns")
async def fix_campaign_statuses(
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Find and fix campaigns stuck in inconsistent states.

    - 'active' campaigns with no pending campaign-leads left  -> 'completed'
    - 'draft' campaigns older than 30 days                    -> 'cancelled'
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=30)

    result = await db.execute(select(Campaign).where(Campaign.org_id == org_id))
    campaigns = list(result.scalars().all())

    fixes: list[dict] = []

    for c in campaigns:
        status = _norm(c.status)

        if status == "active":
            pending = (await db.execute(
                select(func.count(CampaignLead.lead_id)).where(
                    CampaignLead.campaign_id == c.id,
                    CampaignLead.status == "pending",
                )
            )).scalar() or 0
            if pending == 0:
                c.status = "completed"
                fixes.append({
                    "id": str(c.id),
                    "name": c.name,
                    "from": "active",
                    "to": "completed",
                    "reason": "active campaign with no pending leads",
                })

        elif status == "draft":
            created = c.created_at
            if created is not None:
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created < cutoff:
                    c.status = "cancelled"
                    fixes.append({
                        "id": str(c.id),
                        "name": c.name,
                        "from": "draft",
                        "to": "cancelled",
                        "reason": "draft older than 30 days",
                    })

    if fixes:
        await db.commit()

    return {
        "total_campaigns": len(campaigns),
        "fixed": len(fixes),
        "fixes": fixes,
    }
