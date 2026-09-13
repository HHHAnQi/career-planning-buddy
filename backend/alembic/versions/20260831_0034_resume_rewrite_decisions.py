"""Human-confirmed resume rewrite workflow.

Revision ID: 20260831_0034
Revises: 20260830_0033
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260831_0034"
down_revision: str | None = "20260830_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resume_rewrite_decisions",
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
            "assessment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("resume_assessments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("claim_id", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("original_suggestion", sa.Text(), nullable=False),
        sa.Column("rewrite_text", sa.Text()),
        sa.Column(
            "applied_resume_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("resume_versions.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("assessment_id", "claim_id", name="uq_resume_rewrite_decision_claim"),
        sa.CheckConstraint(
            "status IN ('accepted','rejected','applied')",
            name="ck_resume_rewrite_decisions_status",
        ),
        sa.CheckConstraint(
            "(status = 'rejected' AND rewrite_text IS NULL) OR "
            "(status IN ('accepted','applied') AND rewrite_text IS NOT NULL)",
            name="ck_resume_rewrite_decisions_text",
        ),
    )
    op.create_index(
        "ix_resume_rewrite_decisions_user_decided",
        "resume_rewrite_decisions",
        ["user_id", "decided_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_resume_rewrite_decisions_user_decided", table_name="resume_rewrite_decisions")
    op.drop_table("resume_rewrite_decisions")
