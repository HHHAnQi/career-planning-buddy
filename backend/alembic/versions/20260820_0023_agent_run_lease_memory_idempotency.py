"""Add durable Agent Run leases and Memory decision idempotency."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260820_0023"
down_revision: str | None = "20260819_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("worker_id", sa.String(length=64)))
    op.add_column("agent_runs", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.add_column("agent_runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True)))
    op.add_column(
        "agent_runs",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    # A pre-upgrade process cannot own a lease. Requeue active work so a new
    # lease-aware worker can claim it after deployment.
    op.execute(
        "UPDATE agent_runs SET status = 'pending', started_at = NULL "
        "WHERE status = 'running'"
    )
    op.create_check_constraint(
        "ck_agent_runs_running_lease",
        "agent_runs",
        "(status <> 'running') OR "
        "(worker_id IS NOT NULL AND lease_expires_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_agent_runs_attempt_count",
        "agent_runs",
        "attempt_count >= 0",
    )
    op.create_index(
        "ix_agent_runs_claim",
        "agent_runs",
        ["status", "lease_expires_at", "created_at"],
    )

    op.add_column(
        "memory_candidates",
        sa.Column("decision_idempotency_key", sa.String(length=64)),
    )
    op.add_column(
        "memory_candidates",
        sa.Column("decision_request_hash", sa.String(length=64)),
    )
    op.add_column(
        "memory_candidates",
        sa.Column("decision_action", sa.String(length=16)),
    )
    op.create_unique_constraint(
        "uq_memory_candidates_user_decision_idempotency",
        "memory_candidates",
        ["user_id", "decision_idempotency_key"],
    )
    op.create_check_constraint(
        "ck_memory_candidates_decision_idempotency",
        "memory_candidates",
        "(decision_idempotency_key IS NULL AND decision_request_hash IS NULL "
        "AND decision_action IS NULL) OR "
        "(decision_idempotency_key IS NOT NULL AND "
        "decision_request_hash ~ '^[0-9a-f]{64}$' AND "
        "decision_action IN ('confirm','reject'))",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_memory_candidates_decision_idempotency",
        "memory_candidates",
        type_="check",
    )
    op.drop_constraint(
        "uq_memory_candidates_user_decision_idempotency",
        "memory_candidates",
        type_="unique",
    )
    op.drop_column("memory_candidates", "decision_action")
    op.drop_column("memory_candidates", "decision_request_hash")
    op.drop_column("memory_candidates", "decision_idempotency_key")

    op.drop_index("ix_agent_runs_claim", table_name="agent_runs")
    op.drop_constraint("ck_agent_runs_attempt_count", "agent_runs", type_="check")
    op.drop_constraint("ck_agent_runs_running_lease", "agent_runs", type_="check")
    op.drop_column("agent_runs", "attempt_count")
    op.drop_column("agent_runs", "heartbeat_at")
    op.drop_column("agent_runs", "lease_expires_at")
    op.drop_column("agent_runs", "worker_id")
