"""Add explicit navigation intent and result contracts."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260821_0024"
down_revision: str | None = "20260820_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_agent_runs_result_kind", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_result_kind",
        "agent_runs",
        "result_kind IS NULL OR "
        "result_kind IN ('plan','clarification','safe_response','navigation')",
    )
    op.drop_constraint("ck_agent_runs_resolved_intent", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_resolved_intent",
        "agent_runs",
        "resolved_intent IS NULL OR "
        "resolved_intent IN ('create_plan','replan','navigate','unsupported')",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE agent_runs SET resolved_intent = 'unsupported' "
        "WHERE resolved_intent = 'navigate'"
    )
    op.execute(
        "UPDATE agent_runs SET result_kind = 'clarification', "
        "fallback_reason = 'unsupported_intent', "
        "result_payload_json = jsonb_build_object("
        "'questions', jsonb_build_array('请前往计划或任务页面查看。'), "
        "'slot_names', jsonb_build_array('intent'), "
        "'hint_options', jsonb_build_object('intent', jsonb_build_array('create_plan','replan')), "
        "'reason', 'unsupported_intent') "
        "WHERE result_kind = 'navigation'"
    )
    op.drop_constraint("ck_agent_runs_resolved_intent", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_resolved_intent",
        "agent_runs",
        "resolved_intent IS NULL OR "
        "resolved_intent IN ('create_plan','replan','unsupported')",
    )
    op.drop_constraint("ck_agent_runs_result_kind", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_result_kind",
        "agent_runs",
        "result_kind IS NULL OR result_kind IN ('plan','clarification','safe_response')",
    )
