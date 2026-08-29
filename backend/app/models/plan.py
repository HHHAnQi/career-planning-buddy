"""Plan, Task, and companion-message persistence models."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
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


class Plan(Base):
    """Versioned direction plus a fixed cycle of up to seven action days."""

    __tablename__ = "plans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('generated','active','completed','archived')",
            name="ck_plans_status",
        ),
        CheckConstraint("horizon_start <= plan_date", name="ck_plans_start_before_date"),
        CheckConstraint("plan_date <= horizon_end", name="ck_plans_date_before_end"),
        CheckConstraint("parent_plan_id IS NULL OR parent_plan_id <> id", name="ck_plans_not_self"),
        CheckConstraint("version >= 1", name="ck_plans_version"),
        Index("ix_plans_user_date_created", "user_id", "plan_date", "created_at"),
        Index(
            "uq_plans_one_active_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('generated','active')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("agent_runs.id", name="plans_source_run_id_fkey", use_alter=True),
        unique=True,
        nullable=False,
    )
    parent_plan_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("plans.id"),
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="generated")
    plan_date: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_start: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_end: Mapped[date] = mapped_column(Date, nullable=False)
    overall_direction: Mapped[str] = mapped_column(String(500), nullable=False)
    weekly_focus_json: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    adjustment_reason: Mapped[str | None] = mapped_column(Text)
    assumptions_json: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    evidence_refs_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    adopted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class Task(Base):
    """One executable action belonging to a Plan."""

    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint(
            "plan_id", "scheduled_date", "order_index", name="uq_tasks_plan_date_order"
        ),
        CheckConstraint(
            "task_type IN ('learning','project','interview','application','resume','other')",
            name="ck_tasks_type",
        ),
        CheckConstraint(
            "state IN ('pending','in_progress','completed','abandoned','expired')",
            name="ck_tasks_state",
        ),
        CheckConstraint("estimated_minutes BETWEEN 5 AND 480", name="ck_tasks_estimated"),
        CheckConstraint("actual_minutes IS NULL OR actual_minutes >= 0", name="ck_tasks_actual"),
        CheckConstraint(
            "verification_status IN ('not_ready','ready','failed','passed')",
            name="ck_tasks_verification_status",
        ),
        CheckConstraint(
            "(verification_status = 'passed') = deliverable_verified",
            name="ck_tasks_verification_consistency",
        ),
        CheckConstraint(
            "abandoned_reason IS NULL OR abandoned_reason IN "
            "('too_hard','too_easy','no_time','lost_interest','blocked','other')",
            name="ck_tasks_abandoned_reason",
        ),
        CheckConstraint(
            "(state = 'completed' AND actual_minutes IS NOT NULL "
            "AND abandoned_reason IS NULL AND abandoned_reason_text IS NULL) OR "
            "(state = 'abandoned' AND actual_minutes IS NULL "
            "AND abandoned_reason IS NOT NULL AND "
            "((abandoned_reason = 'other' "
            "AND NULLIF(BTRIM(abandoned_reason_text), '') IS NOT NULL) OR "
            "(abandoned_reason <> 'other' AND abandoned_reason_text IS NULL))) OR "
            "(state NOT IN ('completed','abandoned') AND actual_minutes IS NULL "
            "AND abandoned_reason IS NULL AND abandoned_reason_text IS NULL)",
            name="ck_tasks_state_fields",
        ),
        CheckConstraint("order_index >= 0", name="ck_tasks_order"),
        CheckConstraint("version >= 1", name="ck_tasks_version"),
        Index("ix_tasks_user_date", "user_id", "scheduled_date"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    plan_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    task_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scheduled_date: Mapped[date] = mapped_column(Date, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    starter_action: Mapped[str] = mapped_column(String(240), nullable=False)
    deliverable: Mapped[str] = mapped_column(String(240), nullable=False)
    rationale: Mapped[str | None] = mapped_column(String(500))
    estimated_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_minutes: Mapped[int | None] = mapped_column(Integer)
    completed_step_indexes_json: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    deliverable_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    verification_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="not_ready", server_default="not_ready"
    )
    abandoned_reason: Mapped[str | None] = mapped_column(String(32))
    abandoned_reason_text: Mapped[str | None] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class TaskAdjustmentProposal(Base):
    """Auditable manual or model-proposed change to one pending Task."""

    __tablename__ = "task_adjustment_proposals"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "idempotency_key", name="uq_task_adjustments_user_idempotency"
        ),
        CheckConstraint(
            "status IN ('pending','applied','rejected')",
            name="ck_task_adjustments_status",
        ),
        CheckConstraint(
            "generation_method IN ('manual','rule','model','rule_fallback')",
            name="ck_task_adjustments_generation_method",
        ),
        CheckConstraint("version >= 1", name="ck_task_adjustments_version"),
        Index("ix_task_adjustments_task_created", "task_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    request_text: Mapped[str] = mapped_column(String(1000), nullable=False)
    original_task_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    proposed_patch_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    rationale: Mapped[str] = mapped_column(String(500), nullable=False)
    generation_method: Mapped[str] = mapped_column(String(16), nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(128))
    task_version: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class CompanionMessage(Base):
    """Persisted user-facing companion copy associated with a Run or resource."""

    __tablename__ = "companion_messages"
    __table_args__ = (
        CheckConstraint(
            "run_id IS NOT NULL OR plan_id IS NOT NULL OR task_id IS NOT NULL "
            "OR review_id IS NOT NULL",
            name="ck_companion_messages_association",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
    )
    plan_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("plans.id", ondelete="CASCADE"),
    )
    task_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
    )
    review_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "reviews.id",
            name="fk_companion_messages_review_id",
            ondelete="CASCADE",
            use_alter=True,
        ),
    )
    trigger_tag: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    template_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
