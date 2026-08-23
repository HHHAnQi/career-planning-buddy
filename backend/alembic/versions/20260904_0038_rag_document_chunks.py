"""Add rag_document_chunks for hybrid document retrieval (Stage RAG-1).

Revision ID: 20260904_0038
Revises: 20260903_0037
Create Date: 2026-09-04 10:00:00

Hybrid retrieval: pgvector cosine (semantic) + pg_trgm similarity (lexical,
CJK-safe without zhparser), fused with Reciprocal Rank Fusion in the
repository layer.
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260904_0038"
down_revision: str | Sequence[str] | None = "20260903_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "rag_document_chunks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("doc_kind", sa.String(16), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "embedding", pgvector.sqlalchemy.Vector(dim=1024), nullable=True
        ),
        sa.Column(
            "meta_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "embedding_failed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "source_id", "chunk_index", name="uq_rag_chunks_source_index"
        ),
        sa.CheckConstraint(
            "doc_kind IN ('resume','job_target')",
            name="ck_rag_chunks_doc_kind",
        ),
        sa.CheckConstraint("chunk_index >= 0", name="ck_rag_chunks_index"),
        sa.CheckConstraint(
            "(embedding IS NULL) OR (vector_dims(embedding) = 1024)",
            name="ck_rag_chunks_embedding_dim",
        ),
    )
    op.create_index(
        "ix_rag_chunks_user_kind",
        "rag_document_chunks",
        ["user_id", "doc_kind"],
    )
    op.create_index(
        "ix_rag_chunks_embedding_hnsw",
        "rag_document_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_where=sa.text("embedding IS NOT NULL"),
    )
    op.create_index(
        "ix_rag_chunks_content_trgm",
        "rag_document_chunks",
        ["content"],
        postgresql_using="gin",
        postgresql_ops={"content": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_rag_chunks_content_trgm", table_name="rag_document_chunks")
    op.drop_index("ix_rag_chunks_embedding_hnsw", table_name="rag_document_chunks")
    op.drop_index("ix_rag_chunks_user_kind", table_name="rag_document_chunks")
    op.drop_table("rag_document_chunks")
