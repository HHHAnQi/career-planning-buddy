"""Career coach Batch 3 claim assessment and audio analysis.

Revision ID: 20260830_0033
Revises: 20260829_0032
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260830_0033"
down_revision: str | None = "20260829_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_agent_runs_result_kind", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_result_kind",
        "agent_runs",
        "result_kind IS NULL OR result_kind IN "
        "('plan','clarification','safe_response','navigation','interview_turn',"
        "'interview_report','resume_assessment')",
    )
    op.drop_constraint("ck_agent_runs_hint_intent", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_hint_intent",
        "agent_runs",
        "hint_intent IS NULL OR hint_intent IN "
        "('create_plan','replan','interview_start','interview_answer','interview_report',"
        "'resume_assessment')",
    )
    op.drop_constraint("ck_agent_runs_resolved_intent", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_resolved_intent",
        "agent_runs",
        "resolved_intent IS NULL OR resolved_intent IN "
        "('create_plan','replan','navigate','unsupported','interview_start',"
        "'interview_answer','interview_report','resume_assessment')",
    )
    op.drop_constraint("ck_agent_runs_run_kind", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_run_kind",
        "agent_runs",
        "run_kind IN ('planning','interview_start','interview_answer','interview_report',"
        "'resume_assessment')",
    )
    op.drop_constraint("ck_agent_runs_completed_result", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_completed_result",
        "agent_runs",
        "(status <> 'completed') OR (((result_kind = 'plan' AND final_plan_id IS NOT NULL) "
        "OR (result_kind IN ('interview_turn','interview_report','resume_assessment') "
        "AND final_plan_id IS NULL)) AND fallback_reason IS NULL AND error_code IS NULL)",
    )
    op.add_column("interview_turns", sa.Column("audio_analysis_json", postgresql.JSONB()))
    op.create_table(
        "resume_assessments",
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
        sa.Column(
            "interview_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("interview_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "findings_json",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "limitations_json",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id", "idempotency_key", name="uq_resume_assessments_user_idempotency"
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'", name="ck_resume_assessments_request_hash"
        ),
    )
    op.create_index(
        "ix_resume_assessments_user_created",
        "resume_assessments",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_resume_assessments_user_created", table_name="resume_assessments")
    op.drop_table("resume_assessments")
    op.drop_column("interview_turns", "audio_analysis_json")
    op.drop_constraint("ck_agent_runs_completed_result", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_completed_result",
        "agent_runs",
        "(status <> 'completed') OR (((result_kind = 'plan' AND final_plan_id IS NOT NULL) "
        "OR (result_kind IN ('interview_turn','interview_report') AND final_plan_id IS NULL)) "
        "AND fallback_reason IS NULL AND error_code IS NULL)",
    )
    op.drop_constraint("ck_agent_runs_run_kind", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_run_kind",
        "agent_runs",
        "run_kind IN ('planning','interview_start','interview_answer','interview_report')",
    )
    op.drop_constraint("ck_agent_runs_resolved_intent", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_resolved_intent",
        "agent_runs",
        "resolved_intent IS NULL OR resolved_intent IN "
        "('create_plan','replan','navigate','unsupported','interview_start',"
        "'interview_answer','interview_report')",
    )
    op.drop_constraint("ck_agent_runs_hint_intent", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_hint_intent",
        "agent_runs",
        "hint_intent IS NULL OR hint_intent IN "
        "('create_plan','replan','interview_start','interview_answer','interview_report')",
    )
    op.drop_constraint("ck_agent_runs_result_kind", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_result_kind",
        "agent_runs",
        "result_kind IS NULL OR result_kind IN "
        "('plan','clarification','safe_response','navigation','interview_turn',"
        "'interview_report')",
    )
