"""PR-9c.2 Stage B-1a-lite — EvalExperiment.agent_variant (Alembic 0017).

Adds a nullable ``agent_variant VARCHAR(64)`` column to
``eval_experiments`` so experiments can carry an experiment-level
deterministic agent variant identifier (e.g.
``compact_execution_v1`` / ``structured_reasoning_v1``).

The column is nullable for full backward compatibility: existing
experiments (including Stage A) have ``agent_variant IS NULL`` and
follow the legacy MockPlanningProvider path. Only experiments
explicitly tagged with a variant at creation time select the
variant-specific deterministic provider via
``build_planning_provider(settings, agent_variant=...)``.

The variant is included in ``frozen_config_hash`` (see
``EvalService._frozen_hash`` + ``ExperimentCreate.frozen_config()``)
so two experiments differing only by ``agent_variant`` produce
different config hashes — required for the Pairwise variant linkage.

PR-9c.2 Commit 3.5 (Stage B-1a-lite).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260807_0017"
down_revision: str | None = "20260815_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "eval_experiments",
        sa.Column(
            "agent_variant",
            sa.String(64),
            nullable=True,
            comment=(
                "Stage B-1a-lite agent variant identifier "
                "(compact_execution_v1 / structured_reasoning_v1). NULL = legacy."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("eval_experiments", "agent_variant")
