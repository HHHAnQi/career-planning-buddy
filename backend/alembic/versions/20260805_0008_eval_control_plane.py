"""Add the versioned Eval Case/Experiment/Trial/Grade control plane.

Revision ID: 20260805_0008
Revises: 20260804_0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260805_0008"
down_revision: str | None = "20260804_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eval_experiments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("dataset_id", sa.String(64), nullable=False),
        sa.Column("dataset_version", sa.String(128), nullable=False),
        sa.Column("dataset_hash", sa.String(64), nullable=False),
        sa.Column("git_commit", sa.String(64), nullable=False),
        sa.Column("graph_version", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(128), nullable=False),
        sa.Column("model_version", sa.String(128), nullable=False),
        sa.Column("tool_version", sa.String(128), nullable=False),
        sa.Column("context_version", sa.String(128), nullable=False),
        sa.Column("memory_version", sa.String(128), nullable=False),
        sa.Column("frozen_config_hash", sa.String(64), nullable=False),
        sa.Column("execution_mode", sa.String(32), nullable=False),
        sa.Column("variant_role", sa.String(16), nullable=False),
        sa.Column(
            "baseline_experiment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eval_experiments.id", ondelete="RESTRICT"),
        ),
        sa.Column("trial_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), server_default="draft", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "execution_mode IN ('mock_provider','fixture_provider','live_provider')",
            name="ck_eval_experiments_execution_mode",
        ),
        sa.CheckConstraint(
            "variant_role IN ('baseline','candidate')", name="ck_eval_experiments_variant_role"
        ),
        sa.CheckConstraint(
            "status IN ('draft','running','completed','failed','cancelled')",
            name="ck_eval_experiments_status",
        ),
        sa.CheckConstraint("trial_count > 0", name="ck_eval_experiments_trial_count"),
        sa.CheckConstraint(
            "baseline_experiment_id IS NULL OR baseline_experiment_id <> id",
            name="ck_eval_experiments_not_self_baseline",
        ),
        sa.CheckConstraint(
            "(variant_role = 'baseline' AND baseline_experiment_id IS NULL) OR "
            "(variant_role = 'candidate' AND baseline_experiment_id IS NOT NULL)",
            name="ck_eval_experiments_baseline_role",
        ),
        sa.CheckConstraint(
            "dataset_id <> '' AND dataset_version <> '' AND graph_version <> '' "
            "AND prompt_version <> '' AND model_version <> '' AND tool_version <> '' "
            "AND context_version <> '' AND memory_version <> ''",
            name="ck_eval_experiments_versions_present",
        ),
        sa.CheckConstraint(
            "dataset_hash ~ '^[0-9a-f]{64}$' AND frozen_config_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_experiments_hashes",
        ),
    )
    op.create_index(
        "ix_eval_experiments_dataset_created",
        "eval_experiments",
        ["dataset_id", "dataset_version", "created_at"],
    )
    op.create_index(
        "ix_eval_experiments_status_created", "eval_experiments", ["status", "created_at"]
    )

    op.create_table(
        "eval_trials",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "experiment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eval_experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("case_fixture_hash", sa.String(64), nullable=False),
        sa.Column("trial_index", sa.Integer(), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("run_type", sa.String(32), server_default="evaluation", nullable=False),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="RESTRICT"),
        ),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("outcome_snapshot_json", postgresql.JSONB()),
        sa.Column("transcript_hash", sa.String(64)),
        sa.Column("tokens_in", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tokens_out", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost_amount", sa.Numeric(14, 6), server_default="0", nullable=False),
        sa.Column("cost_currency", sa.String(3), server_default="CNY", nullable=False),
        sa.Column("pricing_version", sa.String(64)),
        sa.Column("latency_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "experiment_id", "case_id", "trial_index", name="uq_eval_trials_case_index"
        ),
        sa.CheckConstraint("trial_index >= 0", name="ck_eval_trials_trial_index"),
        sa.CheckConstraint("seed >= 0", name="ck_eval_trials_seed"),
        sa.CheckConstraint(
            "run_type IN ('evaluation','fixture_replay','live_rerun','candidate_backtest')",
            name="ck_eval_trials_run_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending','running','completed','failed','timed_out','cancelled')",
            name="ck_eval_trials_status",
        ),
        sa.CheckConstraint("tokens_in >= 0 AND tokens_out >= 0", name="ck_eval_trials_tokens"),
        sa.CheckConstraint("latency_ms >= 0", name="ck_eval_trials_latency"),
        sa.CheckConstraint("cost_amount >= 0", name="ck_eval_trials_cost"),
        sa.CheckConstraint(
            "case_fixture_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_trials_fixture_hash",
        ),
        sa.CheckConstraint(
            "status <> 'completed' OR (run_id IS NOT NULL AND outcome_snapshot_json IS NOT NULL "
            "AND transcript_hash ~ '^[0-9a-f]{64}$')",
            name="ck_eval_trials_completed_outcome",
        ),
    )
    op.create_index(
        "ix_eval_trials_experiment_status", "eval_trials", ["experiment_id", "status"]
    )
    op.create_index("ix_eval_trials_run_id", "eval_trials", ["run_id"])

    op.create_table(
        "eval_scores",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "trial_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eval_trials.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("grader_name", sa.String(128), nullable=False),
        sa.Column("grader_version", sa.String(128), nullable=False),
        sa.Column("domain", sa.String(16), nullable=False),
        sa.Column("metric_type", sa.String(16), nullable=False),
        sa.Column("score", sa.Float()),
        sa.Column("passed", sa.Boolean()),
        sa.Column("categorical_value", sa.String(128)),
        sa.Column("hard_gate", sa.Boolean(), nullable=False),
        sa.Column("threshold", sa.Float()),
        sa.Column(
            "evidence_item_ids",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=False),
        sa.Column("rationale", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "trial_id", "grader_name", "grader_version", name="uq_eval_scores_grader_version"
        ),
        sa.CheckConstraint(
            "domain IN ('task','behavioral','tool','model','system','safety')",
            name="ck_eval_scores_domain",
        ),
        sa.CheckConstraint(
            "metric_type IN ('boolean','numeric','categorical')",
            name="ck_eval_scores_metric_type",
        ),
        sa.CheckConstraint(
            "(metric_type = 'boolean' AND passed IS NOT NULL AND score IS NULL "
            "AND categorical_value IS NULL) OR "
            "(metric_type = 'numeric' AND score IS NOT NULL AND categorical_value IS NULL) OR "
            "(metric_type = 'categorical' AND score IS NULL AND categorical_value IS NOT NULL)",
            name="ck_eval_scores_typed_value",
        ),
        sa.CheckConstraint("NOT hard_gate OR passed IS NOT NULL", name="ck_eval_scores_hard_gate"),
        sa.CheckConstraint(
            "metric_type = 'numeric' OR threshold IS NULL", name="ck_eval_scores_threshold"
        ),
    )
    op.create_index("ix_eval_scores_trial_domain", "eval_scores", ["trial_id", "domain"])

    op.execute(
        """
        CREATE FUNCTION enforce_eval_experiment_update() RETURNS trigger AS $$
        BEGIN
          IF OLD.status <> NEW.status AND NOT (
            (OLD.status = 'draft' AND NEW.status IN ('running','cancelled')) OR
            (OLD.status = 'running' AND NEW.status IN ('completed','failed','cancelled'))
          ) THEN
            RAISE EXCEPTION 'illegal eval experiment status transition: % -> %',
              OLD.status, NEW.status;
          END IF;
          IF NEW.status <> 'draft' AND (
            OLD.dataset_id IS DISTINCT FROM NEW.dataset_id OR
            OLD.dataset_version IS DISTINCT FROM NEW.dataset_version OR
            OLD.dataset_hash IS DISTINCT FROM NEW.dataset_hash OR
            OLD.git_commit IS DISTINCT FROM NEW.git_commit OR
            OLD.graph_version IS DISTINCT FROM NEW.graph_version OR
            OLD.prompt_version IS DISTINCT FROM NEW.prompt_version OR
            OLD.model_version IS DISTINCT FROM NEW.model_version OR
            OLD.tool_version IS DISTINCT FROM NEW.tool_version OR
            OLD.context_version IS DISTINCT FROM NEW.context_version OR
            OLD.memory_version IS DISTINCT FROM NEW.memory_version OR
            OLD.execution_mode IS DISTINCT FROM NEW.execution_mode OR
            OLD.variant_role IS DISTINCT FROM NEW.variant_role OR
            OLD.baseline_experiment_id IS DISTINCT FROM NEW.baseline_experiment_id OR
            OLD.trial_count IS DISTINCT FROM NEW.trial_count OR
            OLD.frozen_config_hash IS DISTINCT FROM NEW.frozen_config_hash
          ) THEN
            RAISE EXCEPTION 'started eval experiment configuration is immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_enforce_eval_experiment_update
        BEFORE UPDATE ON eval_experiments
        FOR EACH ROW EXECUTE FUNCTION enforce_eval_experiment_update()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_eval_trial_transition() RETURNS trigger AS $$
        BEGIN
          IF OLD.status <> NEW.status AND NOT (
            (OLD.status = 'pending' AND NEW.status IN ('running','cancelled')) OR
            (OLD.status = 'running' AND NEW.status IN
              ('completed','failed','timed_out','cancelled'))
          ) THEN
            RAISE EXCEPTION 'illegal eval trial status transition: % -> %', OLD.status, NEW.status;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_enforce_eval_trial_transition
        BEFORE UPDATE ON eval_trials
        FOR EACH ROW EXECUTE FUNCTION enforce_eval_trial_transition()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_eval_score_completed_trial() RETURNS trigger AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM eval_trials
            WHERE id = NEW.trial_id AND status = 'completed' AND run_id IS NOT NULL
          ) THEN
            RAISE EXCEPTION 'cannot grade an unexecuted or incomplete eval trial';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_enforce_eval_score_completed_trial
        BEFORE INSERT OR UPDATE ON eval_scores
        FOR EACH ROW EXECUTE FUNCTION enforce_eval_score_completed_trial()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_enforce_eval_score_completed_trial ON eval_scores")
    op.execute("DROP FUNCTION enforce_eval_score_completed_trial()")
    op.execute("DROP TRIGGER trg_enforce_eval_trial_transition ON eval_trials")
    op.execute("DROP FUNCTION enforce_eval_trial_transition()")
    op.execute("DROP TRIGGER trg_enforce_eval_experiment_update ON eval_experiments")
    op.execute("DROP FUNCTION enforce_eval_experiment_update()")
    op.drop_table("eval_scores")
    op.drop_table("eval_trials")
    op.drop_table("eval_experiments")
