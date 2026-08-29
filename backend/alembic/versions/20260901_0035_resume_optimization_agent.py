"""Resume optimization Agent runtime and context manifest.

Revision ID: 20260901_0035
Revises: 20260831_0034
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260901_0035"
down_revision: str | None = "20260831_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace_run_constraints(*, include_resume_optimization: bool) -> None:
    suffix = ",'resume_optimization'" if include_resume_optimization else ""
    op.drop_constraint("ck_agent_runs_result_kind", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_result_kind",
        "agent_runs",
        "result_kind IS NULL OR result_kind IN "
        "('plan','clarification','safe_response','navigation','interview_turn',"
        f"'interview_report','resume_assessment'{suffix})",
    )
    for constraint, column, values in (
        (
            "ck_agent_runs_hint_intent",
            "hint_intent",
            "'create_plan','replan','interview_start','interview_answer',"
            "'interview_report','resume_assessment'",
        ),
        (
            "ck_agent_runs_resolved_intent",
            "resolved_intent",
            "'create_plan','replan','navigate','unsupported','interview_start',"
            "'interview_answer','interview_report','resume_assessment'",
        ),
        (
            "ck_agent_runs_run_kind",
            "run_kind",
            "'planning','interview_start','interview_answer','interview_report',"
            "'resume_assessment'",
        ),
    ):
        op.drop_constraint(constraint, "agent_runs", type_="check")
        op.create_check_constraint(
            constraint,
            "agent_runs",
            f"{column} IS NULL OR {column} IN ({values}{suffix})"
            if column != "run_kind"
            else f"{column} IN ({values}{suffix})",
        )
    op.drop_constraint("ck_agent_runs_completed_result", "agent_runs", type_="check")
    result_values = "'interview_turn','interview_report','resume_assessment'"
    if include_resume_optimization:
        result_values += ",'resume_optimization'"
    op.create_check_constraint(
        "ck_agent_runs_completed_result",
        "agent_runs",
        "(status <> 'completed') OR (((result_kind = 'plan' AND final_plan_id IS NOT NULL) "
        f"OR (result_kind IN ({result_values}) AND final_plan_id IS NULL)) "
        "AND fallback_reason IS NULL AND error_code IS NULL)",
    )


def upgrade() -> None:
    _replace_run_constraints(include_resume_optimization=True)
    op.add_column(
        "resume_assessments",
        sa.Column(
            "source_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
        ),
    )
    op.create_unique_constraint(
        "uq_resume_assessments_source_run", "resume_assessments", ["source_run_id"]
    )
    op.add_column(
        "resume_assessments",
        sa.Column(
            "context_manifest_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("resume_assessments", "context_manifest_json")
    op.drop_constraint(
        "uq_resume_assessments_source_run", "resume_assessments", type_="unique"
    )
    op.drop_column("resume_assessments", "source_run_id")
    _replace_run_constraints(include_resume_optimization=False)
