"""Stage 4 long-term memory and evidence persistence models."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

EMBEDDING_DIMENSION = 1024


class Memory(Base):
    """One user-confirmed active or closed long-term memory."""

    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint(
            "memory_type IN ('profile_fact','stable_preference','execution_pattern')",
            name="ck_memories_type",
        ),
        CheckConstraint(
            "sensitivity IN ('normal','sensitive')",
            name="ck_memories_sensitivity",
        ),
        CheckConstraint("status IN ('active','closed')", name="ck_memories_status"),
        CheckConstraint("version >= 1", name="ck_memories_version"),
        Index("ix_memories_user_status_type", "user_id", "status", "memory_type"),
        Index(
            "ix_memories_active_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_where=text("status = 'active' AND embedding IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    content_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSION))
    source_run_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class MemoryCandidate(Base):
    """Sensitive or uncertain long-term memory awaiting user consent."""

    __tablename__ = "memory_candidates"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "decision_idempotency_key",
            name="uq_memory_candidates_user_decision_idempotency",
        ),
        CheckConstraint(
            "memory_type IN ('profile_fact','stable_preference','execution_pattern')",
            name="ck_memory_candidates_type",
        ),
        CheckConstraint(
            "sensitivity IN ('sensitive','highly_sensitive')",
            name="ck_memory_candidates_sensitivity",
        ),
        CheckConstraint(
            "status IN ('pending','confirmed','rejected','expired')",
            name="ck_memory_candidates_status",
        ),
        CheckConstraint(
            "(decision_idempotency_key IS NULL AND decision_request_hash IS NULL "
            "AND decision_action IS NULL) OR "
            "(decision_idempotency_key IS NOT NULL AND "
            "decision_request_hash ~ '^[0-9a-f]{64}$' AND "
            "decision_action IN ('confirm','reject'))",
            name="ck_memory_candidates_decision_idempotency",
        ),
        Index("ix_memory_candidates_user_status", "user_id", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    content_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    proposed_by_run_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    activated_memory_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("memories.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_idempotency_key: Mapped[str | None] = mapped_column(String(64))
    decision_request_hash: Mapped[str | None] = mapped_column(String(64))
    decision_action: Mapped[str | None] = mapped_column(String(16))


class SearchSource(Base):
    """Cleaned and deduplicated search evidence snapshot for one Run."""

    __tablename__ = "search_sources"
    __table_args__ = (
        UniqueConstraint("run_id", "url_hash", name="uq_search_sources_run_url_hash"),
        CheckConstraint(
            "source_type IN ('official','job_board','blog','community','other')",
            name="ck_search_sources_type",
        ),
        CheckConstraint("reliability BETWEEN 0 AND 1", name="ck_search_sources_reliability"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(300))
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    reliability: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(200))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExperienceAtomCandidate(Base):
    """Source-backed reusable knowledge awaiting developer review."""

    __tablename__ = "experience_atom_candidates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected','expired')",
            name="ck_experience_atom_candidates_status",
        ),
        UniqueConstraint("content_hash", name="uq_experience_atom_candidates_content_hash"),
        Index("ix_experience_atom_candidates_status_created", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    goal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(String(300), nullable=False)
    source_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    evidence_excerpt: Mapped[str] = mapped_column(String(300), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    proposed_by_run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE")
    )
    approved_atom_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("experience_atoms.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExperienceAtom(Base):
    """Curated reusable career evidence retrievable by goal and vector similarity."""

    __tablename__ = "experience_atoms"
    __table_args__ = (
        CheckConstraint(
            "goal_type IN ('agent_app','backend','frontend','data','product','other')",
            name="ck_experience_atoms_goal_type",
        ),
        Index("ix_experience_atoms_goal_active", "goal_type", "is_active"),
        Index(
            "ix_experience_atoms_active_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_where=text("is_active AND embedding IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    goal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSION))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
