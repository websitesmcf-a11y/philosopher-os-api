from fastapi import APIRouter, Depends, Query, UploadFile, File, HTTPException
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from app.database.session import get_db
from app.core.security import get_current_user, get_current_org
from app.schemas.knowledge import KnowledgeBaseCreate, KnowledgeBaseUpdate
from app.services.knowledge_service import KnowledgeService
from app.database.models import KnowledgeBase, Lead, Client, Campaign, Conversation, Message, Integration
import uuid as _uuid

router = APIRouter()


@router.get("/graph")
async def get_knowledge_graph(
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    result = await db.execute(
        select(KnowledgeBase).where(KnowledgeBase.org_id == org_id)
    )
    entries = result.scalars().all()

    nodes = [
        {
            "id": str(e.id),
            "title": e.title,
            "content": e.content,
            "category": e.category or "general",
            "tags": e.tags or [],
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in entries
    ]

    edges = []
    if len(entries) > 1 and any(e.embedding is not None for e in entries):
        sql = text("""
            SELECT
                a.id::text AS source,
                b.id::text AS target,
                1 - (a.embedding <=> b.embedding) AS similarity
            FROM knowledge_base a
            JOIN knowledge_base b ON a.id < b.id
            WHERE a.org_id = :org_id
              AND b.org_id = :org_id
              AND a.embedding IS NOT NULL
              AND b.embedding IS NOT NULL
              AND 1 - (a.embedding <=> b.embedding) > 0.65
            ORDER BY similarity DESC
            LIMIT 60
        """)
        rows = (await db.execute(sql, {"org_id": org_id})).fetchall()
        edges = [
            {"source": r[0], "target": r[1], "similarity": round(float(r[2]), 3)}
            for r in rows
        ]

    return {"nodes": nodes, "edges": edges}


@router.get("")
async def search_knowledge(
    q: Optional[str] = None,
    category: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = KnowledgeService(db, org_id=org_id)
    return await service.search(q, page=page, page_size=page_size, category=category)


@router.post("/", status_code=201)
async def add_knowledge(
    data: KnowledgeBaseCreate,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = KnowledgeService(db, org_id=org_id)
    return await service.add_entry(data)


@router.post("/search")
async def semantic_search(
    query: dict,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    q = query.get("query", "")
    service = KnowledgeService(db, org_id=org_id)
    results = await service.semantic_search(q)
    return {"results": results}


@router.delete("/{entry_id}")
async def delete_knowledge(
    entry_id: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    service = KnowledgeService(db, org_id=org_id)
    await service.delete_entry(entry_id)
    return {"deleted": True}


@router.post("/upload", status_code=201)
async def upload_knowledge_file(
    file: UploadFile = File(...),
    title: Optional[str] = None,
    category: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Upload a file (PDF, TXT, MD, DOCX) to the knowledge base.

    The file content is extracted, chunked, and stored with embeddings
    so all council agents can retrieve it via semantic search.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    # Read file content
    content_bytes = await file.read()
    if not content_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    # Extract text based on content type
    ext = (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else ""
    text = ""

    if ext in ("txt", "md", "csv", "json", "xml", "yaml", "yml"):
        text = content_bytes.decode("utf-8", errors="replace")
    elif ext == "pdf":
        try:
            import io
            from PyPDF2 import PdfReader
            pdf = PdfReader(io.BytesIO(content_bytes))
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        except ImportError:
            raise HTTPException(status_code=400, detail="PDF support requires PyPDF2: pip install PyPDF2")
    elif ext == "docx":
        try:
            import io
            from docx import Document
            doc = Document(io.BytesIO(content_bytes))
            text = "\n".join(p.text for p in doc.paragraphs)
        except ImportError:
            raise HTTPException(status_code=400, detail="DOCX support requires python-docx: pip install python-docx")
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: .{ext}. Supported: txt, md, pdf, docx, csv, json")

    if not text.strip():
        raise HTTPException(status_code=400, detail="No extractable text found in file")

    # Truncate very large files to 100KB of text
    if len(text) > 100_000:
        text = text[:100_000] + "\n\n[... truncated ...]"

    # Store as knowledge entry
    entry_title = title or file.filename or "Uploaded file"
    service = KnowledgeService(db, org_id=org_id)
    from app.schemas.knowledge import KnowledgeBaseCreate
    kb_entry = KnowledgeBaseCreate(
        title=entry_title,
        content=text,
        category=category or "uploaded_files",
        tags=[f"file:{file.filename}", f"type:{ext}"] if file.filename else ["uploaded"],
    )
    entry = await service.add_entry(kb_entry)

    return {
        "id": str(entry.id) if hasattr(entry, 'id') else entry.get("id"),
        "title": entry_title,
        "content_length": len(text),
        "file_type": ext,
        "filename": file.filename,
        "message": f"File '{file.filename}' added to knowledge base. All agents can now access this information.",
    }


@router.post("/sync-everything")
async def sync_everything_to_graph(
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(get_current_org),
    user: dict = Depends(get_current_user),
):
    """Pull leads, clients, campaigns, and conversations into the knowledge graph
    so they appear as nodes. Also triggers Obsidian sync if vault is configured."""
    service = KnowledgeService(db, org_id=org_id)
    org_uuid = _uuid.UUID(org_id) if isinstance(org_id, str) else org_id
    added = 0

    # ── Leads → knowledge nodes ──────────────────────────────────────
    leads = (await db.execute(select(Lead).where(Lead.org_id == org_uuid))).scalars().all()
    for lead in leads:
        cf = lead.custom_fields or {}
        title = f"Lead: {lead.company or lead.name}"
        existing = (await db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.org_id == org_uuid,
                KnowledgeBase.title == title,
            )
        )).scalar_one_or_none()
        if not existing:
            content = (
                f"Contact: {lead.name}\n"
                f"Industry: {lead.industry or '—'}\n"
                f"City: {cf.get('city', '—')}\n"
                f"Phone: {lead.phone or '—'}\n"
                f"Email: {lead.email or '—'}\n"
                f"Status: {lead.status or 'new'}\n"
                f"Priority: {cf.get('priority', '—')}\n\n"
                f"{lead.notes or ''}"
            ).strip()
            await service.add_entry(KnowledgeBaseCreate(
                title=title,
                content=content,
                category="leads",
                tags=["lead"] + (lead.tags or []) + ([lead.industry] if lead.industry else []),
            ))
            added += 1

    # ── Clients → knowledge nodes ─────────────────────────────────────
    clients = (await db.execute(select(Client).where(Client.org_id == org_uuid))).scalars().all()
    for client in clients:
        title = f"Client: {client.company or client.name}"
        existing = (await db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.org_id == org_uuid,
                KnowledgeBase.title == title,
            )
        )).scalar_one_or_none()
        if not existing:
            content = (
                f"Contact: {client.name}\n"
                f"Phone: {client.phone or '—'}\n"
                f"Email: {client.email or '—'}\n\n"
                f"{getattr(client, 'notes', '') or ''}"
            ).strip()
            await service.add_entry(KnowledgeBaseCreate(
                title=title,
                content=content,
                category="clients",
                tags=["client"],
            ))
            added += 1

    # ── Campaigns → knowledge nodes ───────────────────────────────────
    camps = (await db.execute(select(Campaign).where(Campaign.org_id == org_uuid))).scalars().all()
    for camp in camps:
        title = f"Campaign: {camp.name}"
        existing = (await db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.org_id == org_uuid,
                KnowledgeBase.title == title,
            )
        )).scalar_one_or_none()
        if not existing:
            content = (
                f"Channel: {camp.channel or '—'}\n"
                f"Status: {camp.status or 'draft'}\n"
                f"Industry: {camp.industry or '—'}\n"
                f"Sent: {camp.sent_count or 0} | Replies: {camp.reply_count or 0}\n\n"
                f"Message Template:\n{camp.message_template or '—'}"
            ).strip()
            await service.add_entry(KnowledgeBaseCreate(
                title=title,
                content=content,
                category="campaigns",
                tags=["campaign", camp.channel or "outreach"],
            ))
            added += 1

    # ── Recent conversations → knowledge nodes ────────────────────────
    convs = (await db.execute(
        select(Conversation)
        .where(Conversation.org_id == org_uuid)
        .order_by(Conversation.last_message_at.desc().nullslast())
        .limit(50)
    )).scalars().all()
    for conv in convs:
        agent = (conv.extra_metadata or {}).get("agent", "unknown")
        when = conv.last_message_at or conv.created_at
        date_str = when.strftime("%Y-%m-%d") if when else "unknown"
        title = f"Chat: {agent} ({date_str})"
        existing = (await db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.org_id == org_uuid,
                KnowledgeBase.title == title,
            )
        )).scalar_one_or_none()
        if not existing:
            msgs = (await db.execute(
                select(Message)
                .where(Message.conversation_id == conv.id)
                .order_by(Message.created_at)
                .limit(20)
            )).scalars().all()
            content_parts = [f"Agent: {agent}", f"Date: {date_str}", ""]
            for msg in msgs:
                role = "User" if msg.sender_type == "user" else agent.title()
                content_parts.append(f"{role}: {(msg.body or '')[:300]}")
            await service.add_entry(KnowledgeBaseCreate(
                title=title,
                content="\n".join(content_parts),
                category="conversations",
                tags=["conversation", agent],
            ))
            added += 1

    # ── Also trigger Obsidian vault sync if configured ────────────────
    obsidian_written = 0
    obsidian_status = "not_configured"
    try:
        result = await db.execute(
            select(Integration).where(
                Integration.provider == "obsidian",
                Integration.org_id == org_uuid,
            )
        )
        obs_row = result.scalar_one_or_none()
        vault_path = (obs_row.config or {}).get("vault_path", "").strip() if obs_row else ""
        if vault_path:
            from app.services.obsidian_sync import sync_vault
            sync_result = await sync_vault(db, vault_path)
            obsidian_written = sync_result.get("written", 0)
            obsidian_status = "synced"
    except Exception as e:
        obsidian_status = f"error: {e}"

    return {
        "added_to_graph": added,
        "leads": len(leads),
        "clients": len(clients),
        "campaigns": len(camps),
        "conversations": len(convs),
        "obsidian": {"status": obsidian_status, "files_written": obsidian_written},
        "message": f"Added {added} new nodes to memory graph. {obsidian_written} files written to Obsidian vault." if obsidian_written else f"Added {added} new nodes to memory graph.",
    }
