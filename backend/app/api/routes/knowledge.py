import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import select

from app.api.dependencies import CurrentUser, DbSession, require_membership
from app.core.config import get_settings
from app.models import KnowledgeDocument
from app.schemas.ai import KnowledgeDocumentListResponse, KnowledgeDocumentResponse
from app.services.knowledge import (
    KnowledgeStoreError,
    document_digest,
    extract_pages,
    get_active_document,
    ingest_document,
    remove_document_vectors,
    sanitize_filename,
    validate_source_url,
)
from app.services.gemini import LLMUnavailableError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/knowledge", tags=["sustainability knowledge"])


@router.post("/documents", response_model=KnowledgeDocumentResponse, status_code=status.HTTP_201_CREATED)
def upload_knowledge_document(
    user: CurrentUser,
    db: DbSession,
    organization_id: UUID = Form(...),
    file: UploadFile = File(...),
    source_title: str | None = Form(default=None, max_length=240),
    source_url: str | None = Form(default=None, max_length=2000),
) -> KnowledgeDocumentResponse:
    require_membership(organization_id, user, db, {"owner", "admin"})
    filename = sanitize_filename(file.filename or "")
    if not filename:
        raise HTTPException(status_code=422, detail="A valid filename is required.")
    try:
        source_url = validate_source_url(source_url)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    content = file.file.read(get_settings().max_upload_size_mb * 1024 * 1024 + 1)
    if len(content) > get_settings().max_upload_size_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Knowledge document exceeds the configured upload limit.")
    try:
        pages = extract_pages(filename, content)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    digest = document_digest(content)
    existing = db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.organization_id == organization_id,
            KnowledgeDocument.content_sha256 == digest,
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="This document has already been uploaded to the organization.")
    document = KnowledgeDocument(
        id=uuid4(),
        organization_id=organization_id,
        uploaded_by_user_id=user.id,
        title=(source_title or filename)[:240],
        filename=filename,
        source_url=source_url,
        content_sha256=digest,
        status="processing",
        chunk_count=0,
    )
    db.add(document)
    db.flush()
    try:
        result = ingest_document(db, document, pages)
    except LLMUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except KnowledgeStoreError as error:
        logger.exception("Knowledge document processing failed for organization %s.", organization_id)
        raise HTTPException(status_code=503, detail=str(error)) from error
    return KnowledgeDocumentResponse.model_validate(result)


@router.get("/documents", response_model=KnowledgeDocumentListResponse)
def list_knowledge_documents(
    organization_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> KnowledgeDocumentListResponse:
    require_membership(organization_id, user, db)
    documents = db.scalars(
        select(KnowledgeDocument)
        .where(
            KnowledgeDocument.organization_id == organization_id,
            KnowledgeDocument.status != "deleted",
        )
        .order_by(KnowledgeDocument.created_at.desc())
    ).all()
    return KnowledgeDocumentListResponse(items=[KnowledgeDocumentResponse.model_validate(item) for item in documents])


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge_document(
    user: CurrentUser,
    db: DbSession,
    document_id: UUID,
    organization_id: UUID = Query(...),
) -> None:
    require_membership(organization_id, user, db, {"owner", "admin"})
    document = get_active_document(db, organization_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Knowledge document not found.")
    try:
        remove_document_vectors(organization_id, document.id)
    except KnowledgeStoreError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    document.status = "deleted"
    db.commit()
