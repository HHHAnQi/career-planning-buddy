"""Persistent human-confirmed planning goal aggregate."""

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


class GoalBrief(Base):
    __tablename__ = "goal_briefs"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_goal_briefs_user_idempotency"),
        CheckConstraint(
            "status IN ('clarification_required','awaiting_confirmation','confirmed','cancelled')",
            name="ck_goal_briefs_status",
        ),
        CheckConstraint(
            "hint_intent IN ('create_plan','replan')", name="ck_goal_briefs_hint_intent"
        ),
        CheckConstraint(
            "duration_weeks IS NULL OR duration_weeks BETWEEN 1 AND 8",
            name="ck_goal_briefs_duration",
        ),
        CheckConstraint(
            "objective_type IS NULL OR objective_type IN "
            "('career_plan','project','application','interview','skill_transition')",
            name="ck_goal_briefs_objective_type",
        ),
        CheckConstraint("version >= 1", name="ck_goal_briefs_version"),
        Index(
            "uq_goal_briefs_one_active_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('clarification_required','awaiting_confirmation')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_message: Mapped[str] = mapped_column(Text, nullable=False)
    hint_intent: Mapped[str] = mapped_column(String(16), nullable=False)
    source_plan_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("plans.id")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    objective_type: Mapped[str | None] = mapped_column(String(32))
    target_role: Mapped[str | None] = mapped_column(String(120))
    objective: Mapped[str | None] = mapped_column(String(500))
    capability_focus_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    tech_stack_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    duration_weeks: Mapped[int | None] = mapped_column(SmallInteger)
    deliverables_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    success_criteria_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    assumptions_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    missing_fields_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    questions_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    extraction_method: Mapped[str] = mapped_column(String(16), nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
