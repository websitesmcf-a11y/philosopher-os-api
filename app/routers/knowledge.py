from fastapi import APIRouter, Depends, Query, UploadFile, File, HTTPException
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.session import get_db
from app.core.security import get_current_user, get_current_org
from app.schemas.knowledge import KnowledgeBaseCreate, KnowledgeBaseUpdate
from app.services.knowledge_service import KnowledgeService

router = APIRouter()


@router.get("/")
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
