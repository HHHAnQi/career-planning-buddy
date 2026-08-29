"""Agent Hardening R2 runtime identity, checkpoints and semantic replay.

Revision ID: 20260902_0036
Revises: 20260901_0035
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260902_0036"
down_revision: str | None = "20260901_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runtime_bundles",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("bundle_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("bundle_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "bundle_hash ~ '^[0-9a-f]{64}$'", name="ck_runtime_bundle_hash"
        ),
    )
    op.add_column(
        "agent_runs", sa.Column("runtime_bundle_id", postgresql.UUID(as_uuid=True))
    )
    op.add_column(
        "agent_runs", sa.Column("resume_version_id", postgresql.UUID(as_uuid=True))
    )
    op.add_column(
        "agent_runs", sa.Column("job_target_id", postgresql.UUID(as_uuid=True))
    )
    op.create_foreign_key(
        "fk_agent_runs_runtime_bundle", "agent_runs", "agent_runtime_bundles",
        ["runtime_bundle_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_agent_runs_resume_version", "agent_runs", "resume_versions",
        ["resume_version_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_agent_runs_job_target", "agent_runs", "job_targets",
        ["job_target_id"], ["id"], ondelete="SET NULL",
    )
    op.alter_column("resume_assessments", "interview_session_id", nullable=True)
    op.create_table(
        "agent_checkpoints",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("node_name", sa.String(64), nullable=False),
        sa.Column("state_json", postgresql.JSONB(), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "run_id", "attempt", "node_name", name="uq_agent_checkpoint_attempt_node"
        ),
        sa.CheckConstraint(
            "state_hash ~ '^[0-9a-f]{64}$'", name="ck_checkpoint_state_hash"
        ),
    )
    op.create_table(
        "replay_comparisons",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "source_run_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id"), nullable=False,
        ),
        sa.Column(
            "replay_run_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id"), nullable=False,
        ),
        sa.Column("comparison_version", sa.String(64), nullable=False),
        sa.Column("semantic_equal", sa.Boolean(), nullable=False),
        sa.Column("diff_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("replay_run_id", name="uq_replay_comparison_replay_run"),
    )


def downgrade() -> None:
    op.drop_table("replay_comparisons")
    op.drop_table("agent_checkpoints")
    op.alter_column("resume_assessments", "interview_session_id", nullable=False)
    op.drop_constraint("fk_agent_runs_job_target", "agent_runs", type_="foreignkey")
    op.drop_constraint("fk_agent_runs_resume_version", "agent_runs", type_="foreignkey")
    op.drop_constraint("fk_agent_runs_runtime_bundle", "agent_runs", type_="foreignkey")
    op.drop_column("agent_runs", "job_target_id")
    op.drop_column("agent_runs", "resume_version_id")
    op.drop_column("agent_runs", "runtime_bundle_id")
    op.drop_table("agent_runtime_bundles")
