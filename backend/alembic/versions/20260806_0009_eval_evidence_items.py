"""Add the per-Trial evidence catalog for the V2 Grader authorization layer.

Revision ID: 20260806_0009
Revises: 20260805_0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID

from alembic import op

revision: str = "20260806_0009"
down_revision: str | None = "20260805_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eval_evidence_items",
        sa.Column(
            "id",
            PostgreSQLUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "trial_id",
            PostgreSQLUUID(as_uuid=True),
            sa.ForeignKey("eval_trials.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("projection_json", JSONB, nullable=False),
        sa.Column(
            "sensitivity",
            sa.String(16),
            nullable=False,
            server_default="normal",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "trial_id",
            "kind",
            "source_type",
            "source_id",
            name="uq_eval_evidence_items_trial_kind_source",
        ),
        sa.CheckConstraint(
            "kind IN ('request_constraints','profile_projection',"
            "'expected_outcome','trajectory_policy','rubric',"
            "'plan_projection','task_projection','step_projection',"
            "'event_projection','tool_call_projection','tool_spec',"
            "'run_metrics','outcome_status','evidence_visible_refs',"
            "'transcript_hash','risk_signals','redacted_output',"
            "'cross_user_signal','tool_allowlist','repair_signal',"
            "'provider_call_projection')",
            name="ck_eval_evidence_items_kind",
        ),
        sa.CheckConstraint(
            "sensitivity IN ('normal','sensitive')",
            name="ck_eval_evidence_items_sensitivity",
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_evidence_items_content_hash",
        ),
    )
    op.create_index(
        "ix_eval_evidence_items_trial_kind",
        "eval_evidence_items",
        ["trial_id", "kind"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_eval_evidence_items_trial_kind", table_name="eval_evidence_items"
    )
    op.drop_table("eval_evidence_items")
