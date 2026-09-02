"""Add Batch 1 resume, job target, interview, and AgentRun contracts."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260828_0031"
down_revision: str | None = "20260827_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("run_kind", sa.String(32), server_default="planning", nullable=False),
    )
    op.add_column(
        "agent_runs",
        sa.Column("interview_session_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "agent_runs", sa.Column("interview_turn_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_check_constraint(
        "ck_agent_runs_run_kind",
        "agent_runs",
        "run_kind IN ('planning','interview_start','interview_answer','interview_report')",
    )
    op.drop_constraint("ck_agent_runs_result_kind", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_result_kind",
        "agent_runs",
        "result_kind IS NULL OR result_kind IN "
        "('plan','clarification','safe_response','navigation',"
        "'interview_turn','interview_report')",
    )
    op.drop_constraint("ck_agent_runs_hint_intent", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_hint_intent",
        "agent_runs",
        "hint_intent IS NULL OR hint_intent IN "
        "('create_plan','replan','interview_start','interview_answer',"
        "'interview_report')",
    )
    op.drop_constraint("ck_agent_runs_resolved_intent", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_resolved_intent",
        "agent_runs",
        "resolved_intent IS NULL OR resolved_intent IN "
        "('create_plan','replan','navigate','unsupported','interview_start',"
        "'interview_answer','interview_report')",
    )
    op.drop_constraint("ck_agent_runs_completed_result", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_completed_result",
        "agent_runs",
        "(status <> 'completed') OR (((result_kind = 'plan' AND final_plan_id IS NOT NULL) "
        "OR (result_kind IN ('interview_turn','interview_report') "
        "AND final_plan_id IS NULL)) AND fallback_reason IS NULL AND error_code IS NULL)",
    )

    op.create_table(
        "resume_versions",
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
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("source_type", sa.String(24), server_default="pasted_text", nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column(
            "structured_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "parent_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("resume_versions.id", ondelete="SET NULL"),
        ),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "user_id", "idempotency_key", name="uq_resume_versions_user_idempotency"
        ),
        sa.CheckConstraint(
            "source_type IN ('pasted_text','uploaded_file')", name="ck_resume_versions_source_type"
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name="ck_resume_versions_content_hash"
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'", name="ck_resume_versions_request_hash"
        ),
    )
    op.create_index("ix_resume_versions_user_created", "resume_versions", ["user_id", "created_at"])

    op.create_table(
        "job_targets",
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
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("company", sa.String(160)),
        sa.Column("jd_text", sa.Text(), nullable=False),
        sa.Column(
            "requirements_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_job_targets_user_idempotency"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_job_targets_content_hash"),
        sa.CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="ck_job_targets_request_hash"),
    )
    op.create_index("ix_job_targets_user_created", "job_targets", ["user_id", "created_at"])

    op.create_table(
        "interview_sessions",
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
        sa.Column(
            "resume_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("resume_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "job_target_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("job_targets.id"),
            nullable=False,
        ),
        sa.Column("interview_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), server_default="draft", nullable=False),
        sa.Column("question_limit", sa.SmallInteger(), server_default="4", nullable=False),
        sa.Column("followup_limit", sa.SmallInteger(), server_default="2", nullable=False),
        sa.Column("asked_question_count", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("followup_count", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("current_turn_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "context_summary_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("report_status", sa.String(24), server_default="not_requested", nullable=False),
        sa.Column("report_version", sa.Integer()),
        sa.Column("report_json", postgresql.JSONB()),
        sa.Column("report_run_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "comparison_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("interview_sessions.id", ondelete="SET NULL"),
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id", "idempotency_key", name="uq_interview_sessions_user_idempotency"
        ),
        sa.CheckConstraint(
            "interview_type IN ('role_focused','resume_deep_dive')",
            name="ck_interview_sessions_type",
        ),
        sa.CheckConstraint(
            "status IN ('draft','active','report_generating','completed','aborted')",
            name="ck_interview_sessions_status",
        ),
        sa.CheckConstraint(
            "report_status IN ('not_requested','generating','ready','failed')",
            name="ck_interview_sessions_report_status",
        ),
        sa.CheckConstraint(
            "question_limit BETWEEN 4 AND 6", name="ck_interview_sessions_question_limit"
        ),
        sa.CheckConstraint(
            "followup_limit BETWEEN 0 AND 2", name="ck_interview_sessions_followup_limit"
        ),
        sa.CheckConstraint(
            "asked_question_count >= 0 AND followup_count >= 0", name="ck_interview_sessions_counts"
        ),
        sa.CheckConstraint("version >= 1", name="ck_interview_sessions_version"),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'", name="ck_interview_sessions_request_hash"
        ),
    )
    op.create_index(
        "ix_interview_sessions_user_created", "interview_sessions", ["user_id", "created_at"]
    )

    op.create_table(
        "interview_turns",
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
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("interview_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.SmallInteger(), nullable=False),
        sa.Column(
            "parent_turn_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("interview_turns.id", ondelete="SET NULL"),
        ),
        sa.Column("topic_key", sa.String(120), nullable=False),
        sa.Column("question_type", sa.String(24), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column(
            "question_sources_json",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("question_fingerprint", sa.String(64), nullable=False),
        sa.Column("answer_text", sa.Text()),
        sa.Column("answer_status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("answer_idempotency_key", sa.String(64)),
        sa.Column("answer_request_hash", sa.String(64)),
        sa.Column("analysis_status", sa.String(16), server_default="not_started", nullable=False),
        sa.Column("analysis_json", postgresql.JSONB()),
        sa.Column("question_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analysis_run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("session_id", "ordinal", name="uq_interview_turns_session_ordinal"),
        sa.CheckConstraint("ordinal >= 1", name="ck_interview_turns_ordinal"),
        sa.CheckConstraint(
            "question_type IN ('technical','project','resume_claim','followup')",
            name="ck_interview_turns_question_type",
        ),
        sa.CheckConstraint(
            "answer_status IN ('pending','submitted','skipped')",
            name="ck_interview_turns_answer_status",
        ),
        sa.CheckConstraint(
            "analysis_status IN ('not_started','running','ready','failed')",
            name="ck_interview_turns_analysis_status",
        ),
        sa.CheckConstraint(
            "question_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_interview_turns_fingerprint"
        ),
        sa.CheckConstraint("version >= 1", name="ck_interview_turns_version"),
    )
    op.create_index(
        "ix_interview_turns_user_session", "interview_turns", ["user_id", "session_id", "ordinal"]
    )

    op.create_foreign_key(
        "fk_interview_sessions_current_turn",
        "interview_sessions",
        "interview_turns",
        ["current_turn_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_interview_sessions_report_run",
        "interview_sessions",
        "agent_runs",
        ["report_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_interview_turns_question_run",
        "interview_turns",
        "agent_runs",
        ["question_run_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_interview_turns_analysis_run",
        "interview_turns",
        "agent_runs",
        ["analysis_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_agent_runs_interview_session",
        "agent_runs",
        "interview_sessions",
        ["interview_session_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_agent_runs_interview_turn",
        "agent_runs",
        "interview_turns",
        ["interview_turn_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_agent_runs_interview_turn", "agent_runs", type_="foreignkey")
    op.drop_constraint("fk_agent_runs_interview_session", "agent_runs", type_="foreignkey")
    op.drop_constraint("fk_interview_turns_analysis_run", "interview_turns", type_="foreignkey")
    op.drop_constraint("fk_interview_turns_question_run", "interview_turns", type_="foreignkey")
    op.drop_constraint("fk_interview_sessions_report_run", "interview_sessions", type_="foreignkey")
    op.drop_constraint(
        "fk_interview_sessions_current_turn", "interview_sessions", type_="foreignkey"
    )
    op.drop_index("ix_interview_turns_user_session", table_name="interview_turns")
    op.drop_table("interview_turns")
    op.drop_index("ix_interview_sessions_user_created", table_name="interview_sessions")
    op.drop_table("interview_sessions")
    op.drop_index("ix_job_targets_user_created", table_name="job_targets")
    op.drop_table("job_targets")
    op.drop_index("ix_resume_versions_user_created", table_name="resume_versions")
    op.drop_table("resume_versions")
    op.drop_constraint("ck_agent_runs_completed_result", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_completed_result",
        "agent_runs",
        "(status <> 'completed') OR (result_kind = 'plan' "
        "AND final_plan_id IS NOT NULL AND fallback_reason IS NULL "
        "AND error_code IS NULL)",
    )
    op.drop_constraint("ck_agent_runs_resolved_intent", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_resolved_intent",
        "agent_runs",
        "resolved_intent IS NULL OR resolved_intent IN "
        "('create_plan','replan','navigate','unsupported')",
    )
    op.drop_constraint("ck_agent_runs_hint_intent", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_hint_intent",
        "agent_runs",
        "hint_intent IS NULL OR hint_intent IN ('create_plan','replan')",
    )
    op.drop_constraint("ck_agent_runs_result_kind", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_result_kind",
        "agent_runs",
        "result_kind IS NULL OR result_kind IN "
        "('plan','clarification','safe_response','navigation')",
    )
    op.drop_constraint("ck_agent_runs_run_kind", "agent_runs", type_="check")
    op.drop_column("agent_runs", "interview_turn_id")
    op.drop_column("agent_runs", "interview_session_id")
    op.drop_column("agent_runs", "run_kind")
