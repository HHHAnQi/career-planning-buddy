"""PR-9c.2 Commit 3.3 — Sweep-level fixture mapping (Alembic 0016).

Stage-A smoke verification requires the ``PairwiseSweepExecutor`` to
build its fixture Judge with a non-empty ``fixture_mapping`` so that
end-to-end verification can exercise the ``completed``-with-winner path
without calling a real LLM. Without somewhere to declare the mapping,
the executor was hard-wired to an empty mapping and every pair returned
``invalid_structured_output`` — that path cannot verify the
review-token / annotation / calibration-report pipeline.

The mapping is stored at the Sweep level (not on the dataset manifest)
because:

* it is an optional metadata field, only populated for fixture-mode
  smoke runs. Production ``pairwise-calibration-v1`` Sweeps MUST leave
  the column NULL;
* it scales with the Sweep (which already carries judge_model_id /
  judge_prompt_version / judge_rubric_version), so adding it respects
  the Sweep-as-judge-identity contract.

Downgrade is a single ``drop_column`` — the column is nullable so the
down path has no data-loss risk.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260815_0016"
down_revision: str | None = "20260815_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "eval_pairwise_sweeps",
        sa.Column(
            "fixture_mapping",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment=(
                "Optional pair_hash → raw_display_winner mapping consumed "
                "by FixturePairwiseJudge when judge_llm_provider='fixture'. "
                "MUST be NULL for production sweeps (live LLM path)."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("eval_pairwise_sweeps", "fixture_mapping")
