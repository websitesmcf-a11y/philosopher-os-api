"""Obsidian vault sync — mirror ALL Socrates data as a second brain."""
import logging
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Conversation, KnowledgeBase, Lead, Client, Campaign, Message

logger = logging.getLogger(__name__)


def _sanitize(name: str) -> str:
    return "".join(c if c.isalnum() or c in " _-.,;:!?" else "_" for c in name).strip() or "untitled"


def _ts(dt) -> str:
    if not dt:
        return "unknown"
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _date(dt) -> str:
    if not dt:
        return "unknown"
    return dt.strftime("%Y-%m-%d")


async def sync_vault(db: AsyncSession, vault_path: str) -> dict:
    """Mirror ALL Socrates data (leads, clients, campaigns, conversations, knowledge)
    into the vault under 'Socrates AI/' as a full second brain."""
    vault = Path(vault_path)
    socrates = vault / "Socrates AI"
    socrates.mkdir(parents=True, exist_ok=True)
    written = 0

    # ── Leads ──────────────────────────────────────────────────────────
    leads_dir = socrates / "Leads"
    leads_dir.mkdir(exist_ok=True)
    leads = (await db.execute(select(Lead).order_by(Lead.created_at.desc()))).scalars().all()
    for lead in leads:
        tags = lead.tags or []
        cf = lead.custom_fields or {}
        fname = _sanitize(f"{lead.company or lead.name}-{lead.id.hex[:6]}") + ".md"
        lines = [
            f"# {lead.company or lead.name}",
            "",
            f"**Contact**: {lead.name}",
            f"**Status**: {lead.status or 'new'}",
            f"**Industry**: {lead.industry or '—'}",
            f"**City**: {cf.get('city', '—')}",
            f"**Phone**: {lead.phone or '—'}",
            f"**Email**: {lead.email or '—'}",
            f"**Website**: {cf.get('website', '—')}",
            f"**Priority**: {cf.get('priority', '—')}",
            f"**Source**: {lead.source or '—'}",
            f"**Tags**: {', '.join(tags) if tags else '—'}",
            f"**Created**: {_date(lead.created_at)}",
            f"**Lead ID**: `{lead.id}`",
            "",
            "---",
            "",
            lead.notes or "_No notes_",
        ]
        (leads_dir / fname).write_text("\n".join(lines), encoding="utf-8")
        written += 1

    # ── Clients ────────────────────────────────────────────────────────
    clients_dir = socrates / "Clients"
    clients_dir.mkdir(exist_ok=True)
    clients = (await db.execute(select(Client).order_by(Client.created_at.desc()))).scalars().all()
    for client in clients:
        fname = _sanitize(f"{client.company or client.name}-{client.id.hex[:6]}") + ".md"
        lines = [
            f"# {client.company or client.name}",
            "",
            f"**Contact**: {client.name}",
            f"**Phone**: {client.phone or '—'}",
            f"**Email**: {client.email or '—'}",
            f"**Company**: {client.company or '—'}",
            f"**Created**: {_date(client.created_at)}",
            f"**Client ID**: `{client.id}`",
            "",
            "---",
            "",
            client.notes or "_No notes_",
        ]
        (clients_dir / fname).write_text("\n".join(lines), encoding="utf-8")
        written += 1

    # ── Campaigns ──────────────────────────────────────────────────────
    camp_dir = socrates / "Campaigns"
    camp_dir.mkdir(exist_ok=True)
    campaigns = (await db.execute(select(Campaign).order_by(Campaign.created_at.desc()))).scalars().all()
    for camp in campaigns:
        fname = _sanitize(f"{camp.name}-{camp.id.hex[:6]}") + ".md"
        lines = [
            f"# {camp.name}",
            "",
            f"**Channel**: {camp.channel or '—'}",
            f"**Status**: {camp.status or 'draft'}",
            f"**Industry**: {camp.industry or '—'}",
            f"**Sent**: {camp.sent_count or 0}  **Replies**: {camp.reply_count or 0}  **Conversions**: {camp.conversion_count or 0}",
            f"**Created**: {_date(camp.created_at)}",
            f"**Campaign ID**: `{camp.id}`",
            "",
            "---",
            "",
            "## Message Template",
            "",
            camp.message_template or "_No template_",
        ]
        (camp_dir / fname).write_text("\n".join(lines), encoding="utf-8")
        written += 1

    # ── Conversations (with full message bodies) ────────────────────────
    conv_dir = socrates / "Conversations"
    conv_dir.mkdir(exist_ok=True)
    convs = (await db.execute(
        select(Conversation).order_by(Conversation.last_message_at.desc().nullslast()).limit(200)
    )).scalars().all()
    for conv in convs:
        agent = (conv.extra_metadata or {}).get("agent", (conv.extra_metadata or {}).get("agent_name", "unknown"))
        when = conv.last_message_at or conv.created_at
        fname = _sanitize(f"{agent}-{_date(when)}-{conv.id.hex[:8]}") + ".md"

        # Load actual messages
        msgs = (await db.execute(
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.created_at)
            .limit(100)
        )).scalars().all()

        lines = [
            f"# Conversation: {agent} ({_date(when)})",
            "",
            f"- **Agent**: {agent}",
            f"- **Channel**: {conv.channel or 'unknown'}",
            f"- **Date**: {_ts(when)}",
            f"- **Status**: {conv.status}",
            f"- **ID**: `{conv.id}`",
            "",
            "---",
            "",
            "## Messages",
            "",
        ]
        for msg in msgs:
            role = "**User**" if msg.sender_type == "user" else f"**{agent.title()}**"
            lines.append(f"{role} — {_ts(msg.created_at)}")
            lines.append("")
            lines.append(msg.body or "_empty_")
            lines.append("")
            lines.append("---")
            lines.append("")

        (conv_dir / fname).write_text("\n".join(lines), encoding="utf-8")
        written += 1

    # ── Knowledge articles ──────────────────────────────────────────────
    know_dir = socrates / "Knowledge"
    know_dir.mkdir(exist_ok=True)
    articles = (await db.execute(select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc()))).scalars().all()
    for art in articles:
        cat_dir = know_dir / _sanitize(art.category or "Uncategorized")
        cat_dir.mkdir(exist_ok=True)
        fname = _sanitize(art.title) + ".md"
        lines = [
            f"# {art.title}",
            "",
            f"- **Category**: {art.category or '—'}",
            f"- **Tags**: {', '.join(art.tags) if art.tags else '—'}",
            f"- **Created**: {_date(art.created_at)}",
            f"- **ID**: `{art.id}`",
            "",
            "---",
            "",
            art.content or "",
        ]
        (cat_dir / fname).write_text("\n".join(lines), encoding="utf-8")
        written += 1

    # ── Master index ────────────────────────────────────────────────────
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    index_lines = [
        f"# Socrates AI — Second Brain Index",
        f"",
        f"Last synced: **{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}**",
        f"",
        f"| Section | Count |",
        f"|---------|-------|",
        f"| Leads | {len(leads)} |",
        f"| Clients | {len(clients)} |",
        f"| Campaigns | {len(campaigns)} |",
        f"| Conversations | {len(convs)} |",
        f"| Knowledge Articles | {len(articles)} |",
        f"| **Total files written** | **{written}** |",
        f"",
        f"## Sections",
        f"",
        f"- [[Leads/]] — all CRM leads",
        f"- [[Clients/]] — active clients",
        f"- [[Campaigns/]] — outreach campaigns",
        f"- [[Conversations/]] — agent chat history",
        f"- [[Knowledge/]] — knowledge base articles",
    ]
    (socrates / "INDEX.md").write_text("\n".join(index_lines), encoding="utf-8")
    written += 1

    logger.info(f"Obsidian sync wrote {written} files to {vault_path}")
    return {
        "written": written,
        "leads": len(leads),
        "clients": len(clients),
        "campaigns": len(campaigns),
        "conversations": len(convs),
        "knowledge": len(articles),
    }
