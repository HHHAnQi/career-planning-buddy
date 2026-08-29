"""Add expected_citations_map to eval_evidence_items.kind CHECK (PR-8b).

Revision ID: 20260809_0012
Revises: 20260808_0011

Widens ck_eval_evidence_items_kind so the new EXPECTED_CITATIONS_MAP
synthetic evidence kind is accepted. The CK is replaced (DROP + ADD)
because PostgreSQL does not support ALTER on CHECK constraints.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260809_0012"
down_revision: str | None = "20260808_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_OLD_LIST = (
    "('request_constraints','profile_projection',"
    "'expected_outcome','trajectory_policy','rubric',"
    "'plan_projection','task_projection','step_projection',"
    "'event_projection','tool_call_projection','tool_spec',"
    "'run_metrics','outcome_status','evidence_visible_refs',"
    "'transcript_hash','risk_signals','redacted_output',"
    "'cross_user_signal','tool_allowlist','repair_signal',"
    "'provider_call_projection')"
)

_NEW_LIST = _OLD_LIST[:-1] + ",'expected_citations_map')"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE eval_evidence_items DROP CONSTRAINT ck_eval_evidence_items_kind"
    )
    op.execute(
        f"ALTER TABLE eval_evidence_items ADD CONSTRAINT ck_eval_evidence_items_kind "
        f"CHECK (kind IN {_NEW_LIST})"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE eval_evidence_items DROP CONSTRAINT ck_eval_evidence_items_kind"
    )
    op.execute(
        f"ALTER TABLE eval_evidence_items ADD CONSTRAINT ck_eval_evidence_items_kind "
        f"CHECK (kind IN {_OLD_LIST})"
    )
