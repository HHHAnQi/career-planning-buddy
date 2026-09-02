"""Persist Agent Run cancellation idempotency metadata."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260819_0022"
down_revision: str | None = "20260818_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("cancel_idempotency_key", sa.String(length=64)),
    )
    op.add_column(
        "agent_runs",
        sa.Column("cancel_request_hash", sa.String(length=64)),
    )
    op.create_check_constraint(
        "ck_agent_runs_cancel_idempotency_pair",
        "agent_runs",
        "(cancel_idempotency_key IS NULL AND cancel_request_hash IS NULL) OR "
        "(cancel_idempotency_key IS NOT NULL AND "
        "cancel_request_hash ~ '^[0-9a-f]{64}$')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_agent_runs_cancel_idempotency_pair", "agent_runs", type_="check"
    )
    op.drop_column("agent_runs", "cancel_request_hash")
    op.drop_column("agent_runs", "cancel_idempotency_key")
