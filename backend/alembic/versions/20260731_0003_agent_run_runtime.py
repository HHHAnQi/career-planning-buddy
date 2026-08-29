"""Create the Stage 2 Agent Run runtime and planning tables.

Revision ID: 20260731_0003
Revises: 20260731_0002
Create Date: 2026-07-31 02:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260731_0003"
down_revision: str | Sequence[str] | None = "20260731_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    """Create the Stage 2 planning resources and durable runtime trace."""
    op.create_table(
        "agent_runs",
        sa.Column("id", _uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", _uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("request_text", sa.Text(), nullable=False),
        sa.Column("hint_intent", sa.String(32)),
        sa.Column("resolved_intent", sa.String(32)),
        sa.Column("replan_mode", sa.String(16)),
        sa.Column("requested_horizon_weeks", sa.SmallInteger()),
        sa.Column("goal_type_override", sa.String(32)),
        sa.Column("source_plan_id", _uuid()),
        sa.Column("source_review_id", _uuid()),
        sa.Column("replay_of_run_id", _uuid()),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("result_kind", sa.String(24)),
        sa.Column("result_payload_json", _jsonb()),
        sa.Column("final_plan_id", _uuid()),
        sa.Column("graph_version", sa.String(64), nullable=False),
        sa.Column("input_snapshot_json", _jsonb()),
        sa.Column("config_snapshot_json", _jsonb(), nullable=False),
        sa.Column("model_id", sa.String(128)),
        sa.Column("total_tokens_in", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_tokens_out", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_cost_cny", sa.Numeric(12, 6), server_default="0", nullable=False),
        sa.Column("total_latency_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("fallback_reason", sa.String(64)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.String(500)),
        sa.Column("risk_category", sa.String(32)),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
        sa.Column("next_event_sequence", sa.Integer(), server_default="1", nullable=False),
        sa.Column("next_step_sequence", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('pending','running','completed','degraded','failed','cancelled')",
            name="ck_agent_runs_status",
        ),
        sa.CheckConstraint(
            "result_kind IS NULL OR result_kind IN ('plan','clarification','safe_response')",
            name="ck_agent_runs_result_kind",
        ),
        sa.CheckConstraint(
            "hint_intent IS NULL OR hint_intent IN ('create_plan','replan')",
            name="ck_agent_runs_hint_intent",
        ),
        sa.CheckConstraint(
            "resolved_intent IS NULL OR resolved_intent IN ('create_plan','replan','unsupported')",
            name="ck_agent_runs_resolved_intent",
        ),
        sa.CheckConstraint(
            "replan_mode IS NULL OR replan_mode IN ('initial','continue','adjust')",
            name="ck_agent_runs_replan_mode",
        ),
        sa.CheckConstraint(
            "requested_horizon_weeks IS NULL OR requested_horizon_weeks BETWEEN 1 AND 8",
            name="ck_agent_runs_horizon",
        ),
        sa.CheckConstraint(
            "(status <> 'completed') OR "
            "(result_kind = 'plan' AND final_plan_id IS NOT NULL "
            "AND fallback_reason IS NULL AND error_code IS NULL)",
            name="ck_agent_runs_completed_result",
        ),
        sa.CheckConstraint(
            "(status <> 'degraded') OR "
            "(result_kind IS NOT NULL AND fallback_reason IS NOT NULL AND error_code IS NULL)",
            name="ck_agent_runs_degraded_result",
        ),
        sa.CheckConstraint(
            "(status NOT IN ('failed','cancelled')) OR "
            "(result_kind IS NULL AND final_plan_id IS NULL AND error_code IS NOT NULL)",
            name="ck_agent_runs_unsuccessful_result",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["replay_of_run_id"], ["agent_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_agent_runs_user_idempotency"),
    )
    op.create_index(
        "uq_agent_runs_one_active_per_user",
        "agent_runs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending','running')"),
    )

    op.create_table(
        "plans",
        sa.Column("id", _uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", _uuid(), nullable=False),
        sa.Column("source_run_id", _uuid(), nullable=False),
        sa.Column("parent_plan_id", _uuid()),
        sa.Column("status", sa.String(16), server_default="generated", nullable=False),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("horizon_start", sa.Date(), nullable=False),
        sa.Column("horizon_end", sa.Date(), nullable=False),
        sa.Column("overall_direction", sa.String(500), nullable=False),
        sa.Column("weekly_focus_json", _jsonb(), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("adjustment_reason", sa.Text()),
        sa.Column(
            "assumptions_json", _jsonb(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column(
            "evidence_refs_json", _jsonb(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column("metadata_json", _jsonb(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("adopted_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('generated','active','completed','archived')",
            name="ck_plans_status",
        ),
        sa.CheckConstraint("horizon_start <= plan_date", name="ck_plans_start_before_date"),
        sa.CheckConstraint("plan_date <= horizon_end", name="ck_plans_date_before_end"),
        sa.CheckConstraint(
            "parent_plan_id IS NULL OR parent_plan_id <> id", name="ck_plans_not_self"
        ),
        sa.CheckConstraint("version >= 1", name="ck_plans_version"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_run_id"], ["agent_runs.id"]),
        sa.ForeignKeyConstraint(["parent_plan_id"], ["plans.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_run_id"),
    )
    op.create_index("ix_plans_user_date_created", "plans", ["user_id", "plan_date", "created_at"])
    op.create_index(
        "uq_plans_one_active_per_user",
        "plans",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('generated','active')"),
    )
    op.create_foreign_key(
        "fk_agent_runs_source_plan_id", "agent_runs", "plans", ["source_plan_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_agent_runs_final_plan_id", "agent_runs", "plans", ["final_plan_id"], ["id"]
    )

    op.create_table(
        "tasks",
        sa.Column("id", _uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("plan_id", _uuid(), nullable=False),
        sa.Column("user_id", _uuid(), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("task_type", sa.String(16), nullable=False),
        sa.Column("scheduled_date", sa.Date(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), server_default="pending", nullable=False),
        sa.Column("starter_action", sa.String(240), nullable=False),
        sa.Column("deliverable", sa.String(240), nullable=False),
        sa.Column("rationale", sa.String(500)),
        sa.Column("estimated_minutes", sa.Integer(), nullable=False),
        sa.Column("actual_minutes", sa.Integer()),
        sa.Column("abandoned_reason", sa.String(32)),
        sa.Column("abandoned_reason_text", sa.String(200)),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("abandoned_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "task_type IN ('learning','project','interview','application','resume','other')",
            name="ck_tasks_type",
        ),
        sa.CheckConstraint(
            "state IN ('pending','in_progress','completed','abandoned','expired')",
            name="ck_tasks_state",
        ),
        sa.CheckConstraint("estimated_minutes BETWEEN 5 AND 480", name="ck_tasks_estimated"),
        sa.CheckConstraint("actual_minutes IS NULL OR actual_minutes >= 0", name="ck_tasks_actual"),
        sa.CheckConstraint("order_index >= 0", name="ck_tasks_order"),
        sa.CheckConstraint("version >= 1", name="ck_tasks_version"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plan_id", "scheduled_date", "order_index", name="uq_tasks_plan_date_order"
        ),
    )
    op.create_index("ix_tasks_user_date", "tasks", ["user_id", "scheduled_date"])

    op.create_table(
        "agent_steps",
        sa.Column("id", _uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", _uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("node_name", sa.String(64), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("prompt_version", sa.String(64)),
        sa.Column("model_id", sa.String(128)),
        sa.Column("tokens_in", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tokens_out", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost_cny", sa.Numeric(12, 6), server_default="0", nullable=False),
        sa.Column("latency_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("input_hash", sa.String(64)),
        sa.Column("output_hash", sa.String(64)),
        sa.Column("trace_data", _jsonb(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('running','completed','failed','skipped')",
            name="ck_agent_steps_status",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_agent_steps_run_sequence"),
    )

    op.create_table(
        "tool_calls",
        sa.Column("id", _uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", _uuid(), nullable=False),
        sa.Column("step_id", _uuid(), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("tool_contract_version", sa.String(32), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("args_json", _jsonb(), nullable=False),
        sa.Column("args_hash", sa.String(64), nullable=False),
        sa.Column("result_json", _jsonb()),
        sa.Column("result_preview", sa.Text()),
        sa.Column("result_hash", sa.String(64)),
        sa.Column("provider", sa.String(32)),
        sa.Column("latency_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["step_id"], ["agent_steps.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tool_calls_run_tool_args", "tool_calls", ["run_id", "tool_name", "args_hash"]
    )

    op.create_table(
        "agent_events",
        sa.Column("id", _uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", _uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload_json", _jsonb(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_agent_events_run_sequence"),
    )
    op.create_index("ix_agent_events_run_sequence", "agent_events", ["run_id", "sequence"])
    op.create_index(
        "uq_agent_events_one_terminal",
        "agent_events",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text(
            "event_type IN ('run.completed','run.degraded','run.failed','run.cancelled')"
        ),
    )

    op.create_table(
        "companion_messages",
        sa.Column("id", _uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", _uuid(), nullable=False),
        sa.Column("run_id", _uuid()),
        sa.Column("plan_id", _uuid()),
        sa.Column("task_id", _uuid()),
        sa.Column("review_id", _uuid()),
        sa.Column("trigger_tag", sa.String(32), nullable=False),
        sa.Column("message", sa.String(1000), nullable=False),
        sa.Column("template_version", sa.String(64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "run_id IS NOT NULL OR plan_id IS NOT NULL OR task_id IS NOT NULL "
            "OR review_id IS NOT NULL",
            name="ck_companion_messages_association",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Remove only Stage 2 resources, preserving Stage 0/1 tables."""
    op.drop_table("companion_messages")
    op.drop_index("uq_agent_events_one_terminal", table_name="agent_events")
    op.drop_index("ix_agent_events_run_sequence", table_name="agent_events")
    op.drop_table("agent_events")
    op.drop_index("ix_tool_calls_run_tool_args", table_name="tool_calls")
    op.drop_table("tool_calls")
    op.drop_table("agent_steps")
    op.drop_index("ix_tasks_user_date", table_name="tasks")
    op.drop_table("tasks")
    op.drop_constraint("fk_agent_runs_final_plan_id", "agent_runs", type_="foreignkey")
    op.drop_constraint("fk_agent_runs_source_plan_id", "agent_runs", type_="foreignkey")
    op.drop_index("uq_plans_one_active_per_user", table_name="plans")
    op.drop_index("ix_plans_user_date_created", table_name="plans")
    op.drop_table("plans")
    op.drop_index("uq_agent_runs_one_active_per_user", table_name="agent_runs")
    op.drop_table("agent_runs")
