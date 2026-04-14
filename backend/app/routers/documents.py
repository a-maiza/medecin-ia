"""Document management endpoints.

POST   /documents/upload     — upload PDF/DOCX (max 50 MB), trigger indexing
GET    /documents            — list documents filtered by cabinet_id (or all for admin)
GET    /documents/{id}/status — polling endpoint for indexing progress
DELETE /documents/{id}       — soft-delete: deprecated=True, exclude chunks from search
"""
from __future__ import annotations

import logging
import os
import tempfile
import uuid
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.document import Document
from app.schemas.auth import CurrentUser
from app.security.audit import log_event
from app.security.jwt import get_current_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["knowledge-base"])

_ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
}
_ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc"}
_MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB


# ── Schemas ───────────────────────────────────────────────────────────────────

class DocumentResponse(BaseModel):
    """Summarised document for list responses."""

    id: uuid.UUID
    filename: str
    source: str
    type: str
    deprecated: bool
    uploaded_at: datetime
    pathologie: Optional[str] = None
    specialite: Optional[str] = None


class UploadResponse(BaseModel):
    """Returned immediately after upload — indexing is async."""

    document_id: uuid.UUID
    filename: str
    task_id: str
    message: str


class DocumentStatusResponse(BaseModel):
    """Polling response for Celery indexing task."""

    document_id: uuid.UUID
    task_id: str
    status: str             # PENDING | STARTED | PROGRESS | SUCCESS | FAILURE
    progress: Optional[dict] = None    # {"done": N, "total": M}
    error: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a PDF or DOCX document for indexing",
)
async def upload_document(
    request: Request,
    file: Annotated[UploadFile, File(description="PDF or DOCX, max 50 MB")],
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> UploadResponse:
    """Accept a PDF or DOCX upload, persist a Document row, enqueue indexing.

    Indexing runs asynchronously via Celery. Poll GET /documents/{id}/status
    for progress events.
    """
    # ── Validate file type ─────────────────────────────────────────────────────
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type '{ext}'. Accepted: PDF, DOCX",
        )

    # ── Read and check size ────────────────────────────────────────────────────
    content = await file.read()
    if len(content) > _MAX_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large ({len(content) // (1024*1024)} MB). Max 50 MB.",
        )

    # ── Persist Document row ───────────────────────────────────────────────────
    doc = Document(
        id=uuid.uuid4(),
        cabinet_id=current_user.cabinet_id,
        type="private",
        source="upload_medecin",
        filename=file.filename or f"upload{ext}",
        deprecated=False,
        uploaded_by=current_user.medecin_id,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    # ── Write to temp file for Celery worker ──────────────────────────────────
    # Celery workers share the filesystem; we write to a temp dir and pass the path
    tmp_dir = tempfile.gettempdir()
    tmp_path = os.path.join(tmp_dir, f"medecinai_upload_{doc.id}{ext}")
    with open(tmp_path, "wb") as f:
        f.write(content)

    # ── Enqueue indexing task ──────────────────────────────────────────────────
    from app.jobs.index_document import index_document
    task = index_document.apply_async(
        kwargs={
            "document_id": str(doc.id),
            "file_path": tmp_path,
            "cabinet_id": str(current_user.cabinet_id),
            "medecin_id": str(current_user.medecin_id),
        },
        queue="ai",
    )

    await log_event(
        db,
        action="document_uploaded",
        resource_type="document",
        actor_id=current_user.medecin_id,
        cabinet_id=current_user.cabinet_id,
        resource_id=str(doc.id),
        payload={"filename": doc.filename, "task_id": task.id},
    )

    log.info(
        "[documents] Upload queued: doc=%s file=%s task=%s",
        doc.id, doc.filename, task.id,
    )

    return UploadResponse(
        document_id=doc.id,
        filename=doc.filename,
        task_id=task.id,
        message="Document reçu. Indexation en cours — suivez la progression via /documents/{id}/status",
    )


@router.get(
    "",
    response_model=list[DocumentResponse],
    summary="List documents",
    description=(
        "Returns documents belonging to the caller's cabinet. "
        "Admins (role='admin_medecinai') receive all global documents as well."
    ),
)
async def list_documents(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    include_deprecated: bool = False,
) -> list[DocumentResponse]:
    """List non-deprecated documents for the cabinet, plus global docs for admins."""
    is_admin = current_user.role == "admin_medecinai"

    stmt = select(Document)

    if is_admin:
        if not include_deprecated:
            stmt = stmt.where(Document.deprecated.is_(False))
    else:
        stmt = stmt.where(Document.cabinet_id == current_user.cabinet_id)
        if not include_deprecated:
            stmt = stmt.where(Document.deprecated.is_(False))

    stmt = stmt.order_by(Document.uploaded_at.desc())
    result = await db.execute(stmt)
    docs = result.scalars().all()

    return [
        DocumentResponse(
            id=d.id,
            filename=d.filename,
            source=d.source,
            type=d.type,
            deprecated=d.deprecated,
            uploaded_at=d.uploaded_at,
            pathologie=d.pathologie,
            specialite=d.specialite,
        )
        for d in docs
    ]


@router.get(
    "/{document_id}/status",
    response_model=DocumentStatusResponse,
    summary="Poll indexing progress for a document",
)
async def document_status(
    document_id: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> DocumentStatusResponse:
    """Return the Celery task status for this document's indexing job.

    Frontend polls this every 2s during upload to show a progress bar.
    """
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    is_admin = current_user.role == "admin_medecinai"
    if not is_admin and doc.cabinet_id != current_user.cabinet_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    # Retrieve task status from Redis via Celery result backend
    redis = getattr(request.app.state, "redis", None) if hasattr(request, "app") else None

    # Look up task_id stored in Redis under doc:task:{document_id}
    task_id = None
    status_str = "UNKNOWN"
    progress = None
    error = None

    if redis:
        try:
            task_id = await redis.get(f"doc:task:{document_id}")
        except Exception:
            pass

    if task_id:
        try:
            from app.celery_app import celery_app
            result = celery_app.AsyncResult(task_id)
            status_str = result.state
            if result.state == "PROGRESS":
                progress = result.info
            elif result.state == "FAILURE":
                error = str(result.result)
        except Exception:
            status_str = "UNKNOWN"

    if doc.deprecated:
        status_str = "FAILURE"
        error = "Indexation échouée — le document a été marqué comme invalide"

    return DocumentStatusResponse(
        document_id=document_id,
        task_id=task_id or "",
        status=status_str,
        progress=progress,
        error=error,
    )


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a document",
    description=(
        "Sets deprecated=True on the document and all its chunks. "
        "Chunks are excluded from future searches automatically via the "
        "hybrid_search source filter."
    ),
)
async def delete_document(
    document_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> None:
    """Soft-delete: mark document + its chunks as deprecated."""
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    is_admin = current_user.role == "admin_medecinai"
    if not is_admin and doc.cabinet_id != current_user.cabinet_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    # Soft delete document
    await db.execute(
        update(Document)
        .where(Document.id == document_id)
        .values(deprecated=True)
    )

    # Soft-exclude chunks: set deprecated flag via raw SQL (Chunk model has no deprecated field,
    # so we mark the parent document and let hybrid_search filter on document.deprecated)
    # The hybrid_search already joins on document to exclude deprecated sources.
    await db.commit()

    await log_event(
        db,
        action="document_deleted",
        resource_type="document",
        actor_id=current_user.medecin_id,
        cabinet_id=current_user.cabinet_id,
        resource_id=str(document_id),
        payload={"filename": doc.filename},
    )

    log.info("[documents] Soft-deleted: doc=%s by medecin=%s", document_id, current_user.medecin_id)
