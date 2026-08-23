"""RAG document chunks for structure-aware hybrid retrieval (Stage RAG-1).

One row per chunk of an ingested user document (resume version or job
target JD). Retrieval is hybrid: pgvector cosine for semantics plus
pg_trgm similarity for lexical overlap (trigram works for CJK without
zhparser), fused with Reciprocal Rank Fusion in the repository layer.
"""

from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RagDocumentChunk(Base):
    """One deterministic chunk of an ingested document, user-isolated."""

    __tablename__ = "rag_document_chunks"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "chunk_index", name="uq_rag_chunks_source_index"
        ),
        Index(
            "ix_rag_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_where=text("embedding IS NOT NULL"),
        ),
        Index(
            "ix_rag_chunks_content_trgm",
            "content",
            postgresql_using="gin",
            postgresql_ops={"content": "gin_trgm_ops"},
        ),
        Index("ix_rag_chunks_user_kind", "user_id", "doc_kind"),
        CheckConstraint(
            "doc_kind IN ('resume','job_target')",
            name="ck_rag_chunks_doc_kind",
        ),
        CheckConstraint("chunk_index >= 0", name="ck_rag_chunks_index"),
        CheckConstraint(
            "(embedding IS NULL) OR (vector_dims(embedding) = 1024)",
            name="ck_rag_chunks_embedding_dim",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[str] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    doc_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # ResumeVersion.id or JobTarget.id.
    source_id: Mapped[str] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(1024), nullable=True
    )
    # Metadata for observability; never trusts model output.
    meta_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Set when the embedding provider failed for this chunk; the chunk
    # stays lexically searchable instead of being dropped.
    embedding_failed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
