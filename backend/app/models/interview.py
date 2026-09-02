"""Interview session and turn persistence models."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class InterviewSession(Base):
    __tablename__ = "interview_sessions"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "idempotency_key", name="uq_interview_sessions_user_idempotency"
        ),
        CheckConstraint(
            "interview_type IN ('role_focused','resume_deep_dive')",
            name="ck_interview_sessions_type",
        ),
        CheckConstraint(
            "status IN ('draft','active','report_generating','completed','aborted')",
            name="ck_interview_sessions_status",
        ),
        CheckConstraint(
            "report_status IN ('not_requested','generating','ready','failed')",
            name="ck_interview_sessions_report_status",
        ),
        CheckConstraint(
            "question_limit BETWEEN 4 AND 6", name="ck_interview_sessions_question_limit"
        ),
        CheckConstraint(
            "followup_limit BETWEEN 0 AND 2", name="ck_interview_sessions_followup_limit"
        ),
        CheckConstraint(
            "asked_question_count >= 0 AND followup_count >= 0", name="ck_interview_sessions_counts"
        ),
        CheckConstraint("version >= 1", name="ck_interview_sessions_version"),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'", name="ck_interview_sessions_request_hash"
        ),
        Index("ix_interview_sessions_user_created", "user_id", "created_at"),
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
    interview_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="draft")
    question_limit: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="4")
    followup_limit: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="2")
    asked_question_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )
    followup_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    current_turn_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "interview_turns.id",
            name="fk_interview_sessions_current_turn",
            use_alter=True,
            ondelete="SET NULL",
        ),
    )
    context_summary_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    report_status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="not_requested"
    )
    report_version: Mapped[int | None] = mapped_column(Integer)
    report_json: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    report_run_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "agent_runs.id",
            name="fk_interview_sessions_report_run",
            use_alter=True,
            ondelete="SET NULL",
        ),
    )
    comparison_session_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("interview_sessions.id", ondelete="SET NULL")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class InterviewTurn(Base):
    __tablename__ = "interview_turns"
    __table_args__ = (
        UniqueConstraint("session_id", "ordinal", name="uq_interview_turns_session_ordinal"),
        CheckConstraint("ordinal >= 1", name="ck_interview_turns_ordinal"),
        CheckConstraint(
            "question_type IN ('technical','project','resume_claim','followup')",
            name="ck_interview_turns_question_type",
        ),
        CheckConstraint(
            "answer_status IN ('pending','submitted','skipped')",
            name="ck_interview_turns_answer_status",
        ),
        CheckConstraint(
            "analysis_status IN ('not_started','running','ready','failed')",
            name="ck_interview_turns_analysis_status",
        ),
        CheckConstraint(
            "question_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_interview_turns_fingerprint"
        ),
        CheckConstraint("version >= 1", name="ck_interview_turns_version"),
        Index("ix_interview_turns_user_session", "user_id", "session_id", "ordinal"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    parent_turn_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("interview_turns.id", ondelete="SET NULL")
    )
    topic_key: Mapped[str] = mapped_column(String(120), nullable=False)
    question_type: Mapped[str] = mapped_column(String(24), nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    question_sources_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    question_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    answer_text: Mapped[str | None] = mapped_column(Text)
    answer_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    answer_idempotency_key: Mapped[str | None] = mapped_column(String(64))
    answer_request_hash: Mapped[str | None] = mapped_column(String(64))
    analysis_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="not_started"
    )
    analysis_json: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    audio_analysis_json: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    question_run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("agent_runs.id", name="fk_interview_turns_question_run", use_alter=True),
        nullable=False,
    )
    analysis_run_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "agent_runs.id",
            name="fk_interview_turns_analysis_run",
            use_alter=True,
            ondelete="SET NULL",
        ),
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
