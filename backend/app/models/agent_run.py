"""Agent Run and persisted runtime trace models."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
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


class AgentRun(Base):
    """One immutable request and its authoritative execution outcome."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_agent_runs_user_idempotency"),
        CheckConstraint(
            "status IN ('pending','running','completed','degraded','failed','cancelled')",
            name="ck_agent_runs_status",
        ),
        CheckConstraint(
            "result_kind IS NULL OR "
            "result_kind IN ('plan','clarification','safe_response','navigation',"
            "'interview_turn','interview_report','resume_assessment','resume_optimization')",
            name="ck_agent_runs_result_kind",
        ),
        CheckConstraint(
            "hint_intent IS NULL OR hint_intent IN "
            "('create_plan','replan','interview_start','interview_answer','interview_report',"
            "'resume_assessment','resume_optimization')",
            name="ck_agent_runs_hint_intent",
        ),
        CheckConstraint(
            "resolved_intent IS NULL OR "
            "resolved_intent IN ('create_plan','replan','navigate','unsupported',"
            "'interview_start','interview_answer','interview_report','resume_assessment',"
            "'resume_optimization')",
            name="ck_agent_runs_resolved_intent",
        ),
        CheckConstraint(
            "run_kind IN ('planning','interview_start','interview_answer','interview_report',"
            "'resume_assessment','resume_optimization')",
            name="ck_agent_runs_run_kind",
        ),
        CheckConstraint(
            "replan_mode IS NULL OR replan_mode IN ('initial','continue','adjust')",
            name="ck_agent_runs_replan_mode",
        ),
        CheckConstraint(
            "requested_horizon_weeks IS NULL OR requested_horizon_weeks BETWEEN 1 AND 8",
            name="ck_agent_runs_horizon",
        ),
        CheckConstraint(
            "(status <> 'completed') OR "
            "(((result_kind = 'plan' AND final_plan_id IS NOT NULL) OR "
            "(result_kind IN ('interview_turn','interview_report','resume_assessment',"
            "'resume_optimization') "
            "AND final_plan_id IS NULL)) AND fallback_reason IS NULL AND error_code IS NULL)",
            name="ck_agent_runs_completed_result",
        ),
        CheckConstraint(
            "(status <> 'degraded') OR "
            "(result_kind IS NOT NULL AND fallback_reason IS NOT NULL AND error_code IS NULL)",
            name="ck_agent_runs_degraded_result",
        ),
        CheckConstraint(
            "(status NOT IN ('failed','cancelled')) OR "
            "(result_kind IS NULL AND final_plan_id IS NULL AND error_code IS NOT NULL)",
            name="ck_agent_runs_unsuccessful_result",
        ),
        CheckConstraint(
            "(cancel_idempotency_key IS NULL AND cancel_request_hash IS NULL) OR "
            "(cancel_idempotency_key IS NOT NULL AND "
            "cancel_request_hash ~ '^[0-9a-f]{64}$')",
            name="ck_agent_runs_cancel_idempotency_pair",
        ),
        CheckConstraint(
            "(status <> 'running') OR (worker_id IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_agent_runs_running_lease",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_agent_runs_attempt_count"),
        Index("ix_agent_runs_claim", "status", "lease_expires_at", "created_at"),
        Index(
            "uq_agent_runs_one_active_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('pending','running')"),
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
    goal_brief_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("goal_briefs.id", ondelete="SET NULL"),
        unique=True,
    )
    run_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="planning"
    )
    interview_session_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "interview_sessions.id",
            name="fk_agent_runs_interview_session",
            use_alter=True,
            ondelete="CASCADE",
        ),
    )
    interview_turn_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "interview_turns.id",
            name="fk_agent_runs_interview_turn",
            use_alter=True,
            ondelete="SET NULL",
        ),
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    request_text: Mapped[str] = mapped_column(Text, nullable=False)
    hint_intent: Mapped[str | None] = mapped_column(String(32))
    resolved_intent: Mapped[str | None] = mapped_column(String(32))
    replan_mode: Mapped[str | None] = mapped_column(String(16))
    requested_horizon_weeks: Mapped[int | None] = mapped_column(SmallInteger)
    goal_type_override: Mapped[str | None] = mapped_column(String(32))
    source_plan_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "plans.id",
            name="fk_agent_runs_source_plan_id",
            use_alter=True,
        ),
    )
    source_review_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "reviews.id",
            name="fk_agent_runs_source_review_id",
            use_alter=True,
        ),
    )
    source_interview_report_session_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "interview_sessions.id",
            name="fk_agent_runs_source_interview_report_session_id",
            use_alter=True,
            ondelete="SET NULL",
        ),
    )
    replay_of_run_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("agent_runs.id"),
    )
    runtime_bundle_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("agent_runtime_bundles.id", ondelete="RESTRICT"),
    )
    resume_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("resume_versions.id", ondelete="SET NULL"),
    )
    job_target_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("job_targets.id", ondelete="SET NULL"),
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    result_kind: Mapped[str | None] = mapped_column(String(24))
    result_payload_json: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    final_plan_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "plans.id",
            name="fk_agent_runs_final_plan_id",
            use_alter=True,
        ),
    )
    graph_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot_json: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    config_snapshot_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(128))
    total_tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_cost_cny: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, server_default="0"
    )
    total_latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    fallback_reason: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(500))
    risk_category: Mapped[str | None] = mapped_column(String(32))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_idempotency_key: Mapped[str | None] = mapped_column(String(64))
    cancel_request_hash: Mapped[str | None] = mapped_column(String(64))
    worker_id: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    next_step_sequence: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentStep(Base):
    """Persisted execution record for one controlled graph node."""

    __tablename__ = "agent_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_agent_steps_run_sequence"),
        CheckConstraint(
            "status IN ('running','completed','failed','skipped')",
            name="ck_agent_steps_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    node_name: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    model_id: Mapped[str | None] = mapped_column(String(128))
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cost_cny: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, server_default="0")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    input_hash: Mapped[str | None] = mapped_column(String(64))
    output_hash: Mapped[str | None] = mapped_column(String(64))
    trace_data: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ToolCall(Base):
    """Replay-safe trace of a whitelisted tool invocation."""

    __tablename__ = "tool_calls"
    __table_args__ = (Index("ix_tool_calls_run_tool_args", "run_id", "tool_name", "args_hash"),)

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("agent_steps.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_contract_version: Mapped[str] = mapped_column(String(32), nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    args_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    args_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    result_preview: Mapped[str | None] = mapped_column(Text)
    result_hash: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str | None] = mapped_column(String(32))
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class AgentEvent(Base):
    """Durable SSE fact for one Run."""

    __tablename__ = "agent_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_agent_events_run_sequence"),
        Index("ix_agent_events_run_sequence", "run_id", "sequence"),
        Index(
            "uq_agent_events_one_terminal",
            "run_id",
            unique=True,
            postgresql_where=text(
                "event_type IN ('run.completed','run.degraded','run.failed','run.cancelled')"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class AgentRuntimeBundle(Base):
    """Content-addressed immutable identity used by live runs and Replay."""

    __tablename__ = "agent_runtime_bundles"
    __table_args__ = (
        CheckConstraint("bundle_hash ~ '^[0-9a-f]{64}$'", name="ck_runtime_bundle_hash"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    bundle_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    bundle_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class AgentCheckpoint(Base):
    """Serializable node output used for bounded resume after lease takeover."""

    __tablename__ = "agent_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "attempt", "node_name", name="uq_agent_checkpoint_attempt_node"
        ),
        CheckConstraint("state_hash ~ '^[0-9a-f]{64}$'", name="ck_checkpoint_state_hash"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    node_name: Mapped[str] = mapped_column(String(64), nullable=False)
    state_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ReplayComparison(Base):
    """Persisted semantic comparison between a source and replay Run."""

    __tablename__ = "replay_comparisons"
    __table_args__ = (
        UniqueConstraint("replay_run_id", name="uq_replay_comparison_replay_run"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    source_run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=False
    )
    replay_run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=False
    )
    comparison_version: Mapped[str] = mapped_column(String(64), nullable=False)
    semantic_equal: Mapped[bool] = mapped_column(Boolean, nullable=False)
    diff_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
