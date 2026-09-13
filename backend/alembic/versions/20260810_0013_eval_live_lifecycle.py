"""PR-9b Eval lifecycle columns (live provider + cancel + report revision).

Revision ID: 20260810_0013
Revises: 20260809_0012

Adds three lightweight INT/timestamp columns to ``eval_experiments`` so
the harness can:

* Stage asynchronous Cancel without confusing it with the terminal
  ``cancelled`` status (``cancel_requested_at``). Pure fact; the
  transaction to ``cancelled`` is still observed separately.
* Track report content revisions that change when the underlying rows
  change (``report_revision`` + ``report_content_hash``). The revision
  counter is content-driven, not call-driven, so repeated regenerate
  calls with unchanged data do not bump the counter.

A partial index on ``cancel_requested_at`` is cheap insurance for the
background executor's cancel-polling path; if the table grows large it
avoids full scans for "any active cancel request?".

Per the PR-9b migration boundary agreement, this migration deliberately
excludes peer_trial_id, seed metadata, retry metadata, and Judge schema:
those belong to PR-9c.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260810_0013"
down_revision: str | None = "20260809_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "eval_experiments",
        sa.Column(
            "cancel_requested_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "eval_experiments",
        sa.Column(
            "report_revision",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "eval_experiments",
        sa.Column(
            "report_content_hash",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.create_index(
        index_name="ix_eval_experiments_cancel_requested",
        table_name="eval_experiments",
        columns=["cancel_requested_at"],
        postgresql_where=sa.text("cancel_requested_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        index_name="ix_eval_experiments_cancel_requested",
        table_name="eval_experiments",
    )
    op.drop_column("eval_experiments", "report_content_hash")
    op.drop_column("eval_experiments", "report_revision")
    op.drop_column("eval_experiments", "cancel_requested_at")
