"""career coach batch 2 report source linkage

Revision ID: 20260829_0032
Revises: 20260828_0031
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260829_0032"
down_revision: str | None = "20260828_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("source_interview_report_session_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_foreign_key(
        "fk_agent_runs_source_interview_report_session_id",
        "agent_runs",
        "interview_sessions",
        ["source_interview_report_session_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_agent_runs_source_interview_report_session_id", "agent_runs", type_="foreignkey"
    )
    op.drop_column("agent_runs", "source_interview_report_session_id")
