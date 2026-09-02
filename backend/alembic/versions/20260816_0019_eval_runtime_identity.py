"""Persist the complete canonical runtime identity for new Eval experiments."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260816_0019"
down_revision: str | None = "20260815_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace_immutability_trigger(*, include_runtime_identity: bool) -> None:
    additions = """
            OLD.feature_stage IS DISTINCT FROM NEW.feature_stage OR
            OLD.search_version IS DISTINCT FROM NEW.search_version OR
            OLD.eval_harness_version IS DISTINCT FROM NEW.eval_harness_version OR
            OLD.agent_variant IS DISTINCT FROM NEW.agent_variant OR
    """ if include_runtime_identity else ""
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION enforce_eval_experiment_update()
        RETURNS trigger AS $$
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
{additions}
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


def upgrade() -> None:
    op.add_column(
        "eval_experiments",
        sa.Column("feature_stage", sa.Integer(), nullable=False, server_default="5"),
    )
    op.add_column(
        "eval_experiments",
        sa.Column(
            "search_version",
            sa.String(128),
            nullable=False,
            server_default="legacy-unversioned",
        ),
    )
    op.add_column(
        "eval_experiments",
        sa.Column(
            "eval_harness_version",
            sa.String(128),
            nullable=False,
            server_default="eval-harness-v2",
        ),
    )
    op.create_check_constraint(
        "ck_eval_experiments_feature_stage",
        "eval_experiments",
        "feature_stage > 0",
    )
    op.drop_constraint(
        "ck_eval_experiments_versions_present",
        "eval_experiments",
        type_="check",
    )
    op.create_check_constraint(
        "ck_eval_experiments_versions_present",
        "eval_experiments",
        "dataset_id <> '' AND dataset_version <> '' AND graph_version <> '' "
        "AND prompt_version <> '' AND model_version <> '' AND tool_version <> '' "
        "AND context_version <> '' AND memory_version <> '' "
        "AND search_version <> '' AND eval_harness_version <> ''",
    )
    _replace_immutability_trigger(include_runtime_identity=True)


def downgrade() -> None:
    _replace_immutability_trigger(include_runtime_identity=False)
    op.drop_constraint(
        "ck_eval_experiments_versions_present",
        "eval_experiments",
        type_="check",
    )
    op.create_check_constraint(
        "ck_eval_experiments_versions_present",
        "eval_experiments",
        "dataset_id <> '' AND dataset_version <> '' AND graph_version <> '' "
        "AND prompt_version <> '' AND model_version <> '' AND tool_version <> '' "
        "AND context_version <> '' AND memory_version <> ''",
    )
    op.drop_constraint(
        "ck_eval_experiments_feature_stage",
        "eval_experiments",
        type_="check",
    )
    op.drop_column("eval_experiments", "eval_harness_version")
    op.drop_column("eval_experiments", "search_version")
    op.drop_column("eval_experiments", "feature_stage")
