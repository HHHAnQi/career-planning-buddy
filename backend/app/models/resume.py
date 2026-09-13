"""Immutable resume versions and frozen job targets for interview sessions."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ResumeVersion(Base):
    __tablename__ = "resume_versions"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_resume_versions_user_idempotency"),
        CheckConstraint(
            "source_type IN ('pasted_text','uploaded_file')", name="ck_resume_versions_source_type"
        ),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_resume_versions_content_hash"),
        CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="ck_resume_versions_request_hash"),
        Index("ix_resume_versions_user_created", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    source_type: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="pasted_text"
    )
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    structured_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("resume_versions.id", ondelete="SET NULL")
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobTarget(Base):
    __tablename__ = "job_targets"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_job_targets_user_idempotency"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_job_targets_content_hash"),
        CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="ck_job_targets_request_hash"),
        Index("ix_job_targets_user_created", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    company: Mapped[str | None] = mapped_column(String(160))
    jd_text: Mapped[str] = mapped_column(Text, nullable=False)
    requirements_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResumeAssessment(Base):
    """Immutable snapshot of one Resume/JD/Interview claim assessment."""

    __tablename__ = "resume_assessments"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "idempotency_key", name="uq_resume_assessments_user_idempotency"
        ),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'", name="ck_resume_assessments_request_hash"
        ),
        Index("ix_resume_assessments_user_created", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    resume_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("resume_versions.id"), nullable=False
    )
    job_target_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("job_targets.id"), nullable=False
    )
    interview_session_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=True,
    )
    source_run_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        unique=True,
    )
    findings_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    limitations_json: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    context_manifest_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ResumeRewriteDecision(Base):
    """Auditable human decision for one Agent-proposed resume rewrite."""

    __tablename__ = "resume_rewrite_decisions"
    __table_args__ = (
        UniqueConstraint("assessment_id", "claim_id", name="uq_resume_rewrite_decision_claim"),
        CheckConstraint(
            "status IN ('accepted','rejected','applied')",
            name="ck_resume_rewrite_decisions_status",
        ),
        CheckConstraint(
            "(status = 'rejected' AND rewrite_text IS NULL) OR "
            "(status IN ('accepted','applied') AND rewrite_text IS NOT NULL)",
            name="ck_resume_rewrite_decisions_text",
        ),
        Index("ix_resume_rewrite_decisions_user_decided", "user_id", "decided_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    assessment_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("resume_assessments.id", ondelete="CASCADE"),
        nullable=False,
    )
    claim_id: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    original_suggestion: Mapped[str] = mapped_column(Text, nullable=False)
    rewrite_text: Mapped[str | None] = mapped_column(Text)
    applied_resume_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("resume_versions.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
