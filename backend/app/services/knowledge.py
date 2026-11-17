import hashlib
import logging
import re
from io import BytesIO
from pathlib import PurePosixPath
from urllib.parse import urlparse
from uuid import UUID

from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import KnowledgeDocument
from app.services.gemini import LLMProviderError, LLMUnavailableError, generate_embeddings

logger = logging.getLogger(__name__)
COLLECTION_NAME = "carbontrace_sustainability"
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 180
EMBEDDING_BATCH_SIZE = 32


class KnowledgeStoreError(RuntimeError):
    pass


def sanitize_filename(filename: str) -> str:
    return PurePosixPath(filename.replace("\\", "/")).name[:255]


def validate_source_url(source_url: str | None) -> str | None:
    if source_url is None or not source_url.strip():
        return None
    value = source_url.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Source URL must use HTTP or HTTPS.")
    return value


def extract_pages(filename: str, content: bytes) -> list[tuple[int | None, str]]:
    extension = PurePosixPath(filename.lower()).suffix
    if extension == ".pdf":
        try:
            reader = PdfReader(BytesIO(content))
            if len(reader.pages) > 500:
                raise ValueError("PDF documents cannot exceed 500 pages.")
            pages = [(index + 1, page.extract_text() or "") for index, page in enumerate(reader.pages)]
        except ValueError:
            raise
        except Exception as error:
            raise ValueError("The PDF could not be read. Check that it is a valid, text-based PDF.") from error
    elif extension in {".txt", ".md"}:
        pages = [(None, content.decode("utf-8-sig"))]
    else:
        raise ValueError("Only PDF, Markdown, and plain-text knowledge documents are supported.")
    if not any(text.strip() for _, text in pages):
        raise ValueError("The document contains no extractable text.")
    return pages


def chunk_pages(pages: list[tuple[int | None, str]]) -> list[tuple[int | None, str]]:
    chunks: list[tuple[int | None, str]] = []
    for page_number, text in pages:
        normalized = re.sub(r"\s+", " ", text).strip()
        start = 0
        while start < len(normalized):
            end = min(start + CHUNK_SIZE, len(normalized))
            if end < len(normalized):
                boundary = normalized.rfind(" ", start, end)
                if boundary > start + CHUNK_SIZE // 2:
                    end = boundary
            chunk = normalized[start:end].strip()
            if chunk:
                chunks.append((page_number, chunk))
            if end >= len(normalized):
                break
            start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks


def _collection():
    settings = get_settings()
    try:
        import chromadb

        client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
        return client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    except Exception as error:
        logger.exception("Could not connect to the configured ChromaDB service.")
        raise KnowledgeStoreError("The sustainability knowledge store is unavailable.") from error


def ingest_document(
    db: Session,
    document: KnowledgeDocument,
    pages: list[tuple[int | None, str]],
) -> KnowledgeDocument:
    chunks = chunk_pages(pages)
    if not chunks:
        raise ValueError("The document contains no extractable text.")
    document.status = "processing"
    db.add(document)
    db.commit()
    try:
        vectors: list[list[float]] = []
        texts = [text for _, text in chunks]
        for offset in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            vectors.extend(
                generate_embeddings(
                    texts[offset : offset + EMBEDDING_BATCH_SIZE],
                    task_type="RETRIEVAL_DOCUMENT",
                )
            )
        collection = _collection()
        ids = [f"{document.id}:{index}" for index in range(len(chunks))]
        metadata = [
            {
                "organization_id": str(document.organization_id),
                "document_id": str(document.id),
                "title": document.title,
                "source_url": document.source_url or "",
                "page": page_number or 0,
                "chunk_index": index,
                "active": False,
            }
            for index, (page_number, _) in enumerate(chunks)
        ]
        collection.add(ids=ids, embeddings=vectors, metadatas=metadata, documents=texts)
        stored = collection.get(
            where={"document_id": str(document.id)},
            include=["metadatas"],
        )
        stored_ids = stored.get("ids", [])
        stored_metadata = stored.get("metadatas", [])
        collection.update(
            ids=stored_ids,
            metadatas=[{**item, "active": True} for item in stored_metadata],
        )
        document.chunk_count = len(chunks)
        document.status = "active"
        document.error_message = None
        db.commit()
        return document
    except (LLMUnavailableError, LLMProviderError, KnowledgeStoreError):
        document.status = "failed"
        document.error_message = "Embedding or vector storage failed; this document is not available for retrieval."
        db.commit()
        raise
    except Exception as error:
        document.status = "failed"
        document.error_message = "Embedding or vector storage failed; this document is not available for retrieval."
        db.commit()
        logger.exception("Knowledge document ingestion failed for document %s.", document.id)
        raise KnowledgeStoreError("Knowledge document ingestion failed.") from error


def retrieve_documents(db: Session, organization_id: UUID, query: str, limit: int = 5) -> list[dict]:
    try:
        query_vector = generate_embeddings([query], task_type="RETRIEVAL_QUERY")[0]
        results = _collection().query(
            query_embeddings=[query_vector],
            n_results=limit,
            where={
                "$and": [
                    {"organization_id": str(organization_id)},
                    {"active": True},
                ]
            },
            include=["documents", "metadatas", "distances"],
        )
    except (LLMUnavailableError, LLMProviderError, KnowledgeStoreError):
        raise
    except Exception as error:
        logger.exception("Sustainability knowledge retrieval failed for organization %s.", organization_id)
        raise KnowledgeStoreError("Sustainability knowledge retrieval failed.") from error
    ids = (results.get("ids") or [[]])[0]
    documents = (results.get("documents") or [[]])[0]
    metadatas = (results.get("metadatas") or [[]])[0]
    distances = (results.get("distances") or [[]])[0]
    document_ids = {
        UUID(metadata["document_id"])
        for metadata in metadatas
        if metadata and metadata.get("document_id")
    }
    active_document_ids = set(
        db.scalars(
            select(KnowledgeDocument.id).where(
                KnowledgeDocument.organization_id == organization_id,
                KnowledgeDocument.id.in_(document_ids),
                KnowledgeDocument.status == "active",
            )
        ).all()
    ) if document_ids else set()
    retrieved: list[dict] = []
    for chunk_id, text, metadata, distance in zip(ids, documents, metadatas, distances):
        if not metadata or not text:
            continue
        if UUID(metadata["document_id"]) not in active_document_ids:
            continue
        page = int(metadata.get("page") or 0)
        retrieved.append(
            {
                "id": f"knowledge:{chunk_id}",
                "kind": "knowledge",
                "title": metadata.get("title", "Sustainability source"),
                "source_url": metadata.get("source_url") or None,
                "page": page or None,
                "text": text,
                "distance": float(distance),
            }
        )
    return retrieved


def remove_document_vectors(organization_id: UUID, document_id: UUID) -> None:
    try:
        collection = _collection()
        existing = collection.get(
            where={
                "$and": [
                    {"organization_id": str(organization_id)},
                    {"document_id": str(document_id)},
                ]
            },
            include=["metadatas"],
        )
        ids = existing.get("ids", [])
        if ids:
            collection.delete(ids=ids)
    except KnowledgeStoreError:
        raise
    except Exception as error:
        logger.exception("Could not remove ChromaDB vectors for document %s.", document_id)
        raise KnowledgeStoreError("The document vectors could not be removed.") from error


def document_digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def get_active_document(db: Session, organization_id: UUID, document_id: UUID) -> KnowledgeDocument | None:
    return db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.organization_id == organization_id,
            KnowledgeDocument.status == "active",
        )
    )
