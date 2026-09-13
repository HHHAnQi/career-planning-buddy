"""Add persistent Goal Brief confirmation gate."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260822_0025"
down_revision: str | None = "20260821_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "goal_briefs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("source_message", sa.Text(), nullable=False),
        sa.Column("hint_intent", sa.String(16), nullable=False),
        sa.Column("source_plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("plans.id")),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("target_role", sa.String(120)),
        sa.Column("project_goal", sa.String(500)),
        sa.Column(
            "capability_focus_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "tech_stack_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("duration_weeks", sa.SmallInteger()),
        sa.Column(
            "deliverables_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "success_criteria_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "assumptions_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "missing_fields_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "questions_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("extraction_method", sa.String(16), nullable=False),
        sa.Column("model_id", sa.String(128)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_goal_briefs_user_idempotency"),
        sa.CheckConstraint(
            "status IN ('clarification_required','awaiting_confirmation','confirmed','cancelled')",
            name="ck_goal_briefs_status",
        ),
        sa.CheckConstraint(
            "hint_intent IN ('create_plan','replan')", name="ck_goal_briefs_hint_intent"
        ),
        sa.CheckConstraint(
            "duration_weeks IS NULL OR duration_weeks BETWEEN 1 AND 8",
            name="ck_goal_briefs_duration",
        ),
        sa.CheckConstraint("version >= 1", name="ck_goal_briefs_version"),
    )
    op.create_index(
        "uq_goal_briefs_one_active_per_user",
        "goal_briefs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('clarification_required','awaiting_confirmation')"),
    )
    op.add_column("agent_runs", sa.Column("goal_brief_id", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key(
        "fk_agent_runs_goal_brief_id",
        "agent_runs",
        "goal_briefs",
        ["goal_brief_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint("uq_agent_runs_goal_brief_id", "agent_runs", ["goal_brief_id"])


def downgrade() -> None:
    op.drop_constraint("uq_agent_runs_goal_brief_id", "agent_runs", type_="unique")
    op.drop_constraint("fk_agent_runs_goal_brief_id", "agent_runs", type_="foreignkey")
    op.drop_column("agent_runs", "goal_brief_id")
    op.drop_index("uq_goal_briefs_one_active_per_user", table_name="goal_briefs")
    op.drop_table("goal_briefs")
