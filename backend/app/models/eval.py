"""Versioned Eval Case execution control-plane models."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class EvalExperiment(Base):
    """Immutable version combination evaluated against one versioned dataset."""

    __tablename__ = "eval_experiments"
    __table_args__ = (
        CheckConstraint(
            "execution_mode IN ('mock_provider','fixture_provider','live_provider')",
            name="ck_eval_experiments_execution_mode",
        ),
        CheckConstraint(
            "variant_role IN ('baseline','candidate')",
            name="ck_eval_experiments_variant_role",
        ),
        CheckConstraint(
            "status IN ('draft','running','completed','failed','cancelled')",
            name="ck_eval_experiments_status",
        ),
        CheckConstraint("trial_count > 0", name="ck_eval_experiments_trial_count"),
        CheckConstraint(
            "baseline_experiment_id IS NULL OR baseline_experiment_id <> id",
            name="ck_eval_experiments_not_self_baseline",
        ),
        CheckConstraint(
            "(variant_role = 'baseline' AND baseline_experiment_id IS NULL) OR "
            "(variant_role = 'candidate' AND baseline_experiment_id IS NOT NULL)",
            name="ck_eval_experiments_baseline_role",
        ),
        CheckConstraint(
            "dataset_id <> '' AND dataset_version <> '' AND graph_version <> '' "
            "AND prompt_version <> '' AND model_version <> '' AND tool_version <> '' "
            "AND context_version <> '' AND memory_version <> '' "
            "AND search_version <> '' AND eval_harness_version <> ''",
            name="ck_eval_experiments_versions_present",
        ),
        CheckConstraint(
            "feature_stage > 0",
            name="ck_eval_experiments_feature_stage",
        ),
        CheckConstraint(
            "dataset_hash ~ '^[0-9a-f]{64}$' AND frozen_config_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_experiments_hashes",
        ),
        Index("ix_eval_experiments_dataset_created", "dataset_id", "dataset_version", "created_at"),
        Index("ix_eval_experiments_status_created", "status", "created_at"),
        Index(
            "ix_eval_experiments_cancel_requested",
            "cancel_requested_at",
            postgresql_where=text("cancel_requested_at IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    dataset_id: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    git_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    graph_version: Mapped[str] = mapped_column(String(128), nullable=False)
    feature_stage: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="5"
    )
    prompt_version: Mapped[str] = mapped_column(String(128), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(128), nullable=False)
    context_version: Mapped[str] = mapped_column(String(128), nullable=False)
    memory_version: Mapped[str] = mapped_column(String(128), nullable=False)
    search_version: Mapped[str] = mapped_column(
        String(128), nullable=False, server_default="legacy-unversioned"
    )
    eval_harness_version: Mapped[str] = mapped_column(
        String(128), nullable=False, server_default="eval-harness-v2"
    )
    frozen_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    variant_role: Mapped[str] = mapped_column(String(16), nullable=False)
    baseline_experiment_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("eval_experiments.id", ondelete="RESTRICT"),
    )
    trial_count: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_variant: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment=(
            "Stage B-1a-lite agent variant identifier "
            "(compact_execution_v1 / structured_reasoning_v1). NULL = legacy."
        ),
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # PR-9b: cancel + report revision columns (migration 0013).
    # ``cancel_requested_at`` is a fact record only; status=='cancelled'
    # is the terminal signal. ``report_revision`` increments only when
    # ``report_content_hash`` changes, never on every regenerate call.
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    report_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    report_content_hash: Mapped[str | None] = mapped_column(String(64))


class EvalTrial(Base):
    """One isolated execution attempt for an Experiment Case."""

    __tablename__ = "eval_trials"
    __table_args__ = (
        # PR-8: enforce uniqueness for NULL-variant (legacy) and non-NULL
        # variant (paired counterfactual) Trials via PostgreSQL partial
        # unique indexes. UniqueConstraint does not accept postgresql_where,
        # so we model both as partial indexes; the legacy
        # ``uq_eval_trials_case_index`` name is preserved on the NULL arm.
        Index(
            "uq_eval_trials_case_index",
            "experiment_id",
            "case_id",
            "trial_index",
            unique=True,
            postgresql_where=text("variant IS NULL"),
        ),
        Index(
            "uq_eval_trials_case_index_variant",
            "experiment_id",
            "case_id",
            "variant",
            "trial_index",
            unique=True,
            postgresql_where=text("variant IS NOT NULL"),
        ),
        CheckConstraint("trial_index >= 0", name="ck_eval_trials_trial_index"),
        CheckConstraint("seed >= 0", name="ck_eval_trials_seed"),
        CheckConstraint(
            "run_type IN ('evaluation','fixture_replay','live_rerun','candidate_backtest')",
            name="ck_eval_trials_run_type",
        ),
        CheckConstraint(
            "status IN ('pending','running','completed','failed','timed_out','cancelled')",
            name="ck_eval_trials_status",
        ),
        CheckConstraint("tokens_in >= 0 AND tokens_out >= 0", name="ck_eval_trials_tokens"),
        CheckConstraint("latency_ms >= 0", name="ck_eval_trials_latency"),
        CheckConstraint("cost_amount >= 0", name="ck_eval_trials_cost"),
        CheckConstraint(
            "case_fixture_hash ~ '^[0-9a-f]{64}$'", name="ck_eval_trials_fixture_hash"
        ),
        CheckConstraint(
            "status <> 'completed' OR (run_id IS NOT NULL AND outcome_snapshot_json IS NOT NULL "
            "AND transcript_hash ~ '^[0-9a-f]{64}$')",
            name="ck_eval_trials_completed_outcome",
        ),
        Index("ix_eval_trials_experiment_status", "experiment_id", "status"),
        Index("ix_eval_trials_run_id", "run_id"),
        Index(
            "ix_eval_trials_fixture_source",
            "fixture_source_trial_id",
            postgresql_where=text("fixture_source_trial_id IS NOT NULL"),
        ),
        Index(
            "ix_eval_trials_group",
            "counterfactual_group_id",
            postgresql_where=text("counterfactual_group_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    experiment_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("eval_experiments.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    case_fixture_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    trial_index: Mapped[int] = mapped_column(Integer, nullable=False)
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # PR-8: counterfactual pairing. ``variant`` identifies which ablation
    # arm the Trial represents within its group; ``counterfactual_group_id``
    # ties two or more variants together for paired-diff reporting. Both
    # are NULL for pre-PR-8 single-arm Trials, which preserves the legacy
    # uniqueness contract (uq_eval_trials_case_index).
    variant: Mapped[str | None] = mapped_column(String(64))
    counterfactual_group_id: Mapped[str | None] = mapped_column(String(64))
    run_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default="evaluation")
    fixture_source_trial_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("eval_trials.id", ondelete="RESTRICT"),
    )
    run_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    outcome_snapshot_json: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    transcript_hash: Mapped[str | None] = mapped_column(String(64))
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cost_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 6), nullable=False, server_default="0"
    )
    cost_currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="CNY")
    pricing_version: Mapped[str | None] = mapped_column(String(64))
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvalScore(Base):
    """Typed, evidence-bearing Grade result for one completed Trial."""

    __tablename__ = "eval_scores"
    __table_args__ = (
        UniqueConstraint(
            "trial_id", "grader_name", "grader_version", name="uq_eval_scores_grader_version"
        ),
        CheckConstraint(
            "domain IN ('task','behavioral','tool','model','system','safety')",
            name="ck_eval_scores_domain",
        ),
        CheckConstraint(
            "metric_type IN ('boolean','numeric','categorical')",
            name="ck_eval_scores_metric_type",
        ),
        CheckConstraint(
            "(metric_type = 'boolean' AND passed IS NOT NULL AND score IS NULL "
            "AND categorical_value IS NULL) OR "
            "(metric_type = 'numeric' AND score IS NOT NULL AND categorical_value IS NULL) OR "
            "(metric_type = 'categorical' AND score IS NULL AND categorical_value IS NOT NULL)",
            name="ck_eval_scores_typed_value",
        ),
        CheckConstraint("NOT hard_gate OR passed IS NOT NULL", name="ck_eval_scores_hard_gate"),
        CheckConstraint(
            "metric_type = 'numeric' OR threshold IS NULL",
            name="ck_eval_scores_threshold",
        ),
        Index("ix_eval_scores_trial_domain", "trial_id", "domain"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    trial_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("eval_trials.id", ondelete="CASCADE"),
        nullable=False,
    )
    grader_name: Mapped[str] = mapped_column(String(128), nullable=False)
    grader_version: Mapped[str] = mapped_column(String(128), nullable=False)
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    metric_type: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    passed: Mapped[bool | None] = mapped_column(Boolean)
    categorical_value: Mapped[str | None] = mapped_column(String(128))
    hard_gate: Mapped[bool] = mapped_column(Boolean, nullable=False)
    threshold: Mapped[float | None] = mapped_column(Float)
    evidence_item_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    evidence_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class EvalEvidenceItem(Base):
    """One piece of frozen evidence fed to a V2 Grader.

    The (trial_id, kind, source_type, source_id) tuple is unique: if the
    underlying projection changed, the new content_hash must NOT silently
    overwrite the old row -- the collector inserts new rows whose ids differ
    from any prior Score's evidence_item_ids, forcing a re-grade.
    """

    __tablename__ = "eval_evidence_items"
    __table_args__ = (
        UniqueConstraint(
            "trial_id",
            "kind",
            "source_type",
            "source_id",
            name="uq_eval_evidence_items_trial_kind_source",
        ),
        CheckConstraint(
            "kind IN ('request_constraints','profile_projection',"
            "'expected_outcome','trajectory_policy','rubric',"
            "'plan_projection','task_projection','step_projection',"
            "'event_projection','tool_call_projection','tool_spec',"
            "'run_metrics','outcome_status','evidence_visible_refs',"
            "'transcript_hash','risk_signals','redacted_output',"
            "'cross_user_signal','tool_allowlist','repair_signal',"
            "'provider_call_projection','expected_citations_map')",
            name="ck_eval_evidence_items_kind",
        ),
        CheckConstraint(
            "sensitivity IN ('normal','sensitive')",
            name="ck_eval_evidence_items_sensitivity",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_evidence_items_content_hash",
        ),
        Index("ix_eval_evidence_items_trial_kind", "trial_id", "kind"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    trial_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("eval_trials.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(128))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    sensitivity: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="normal"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class EvalTrialPair(Base):
    """Stable identity of one baseline-vs-candidate comparison (PR-9c.1).

    One row per *Pair* (NOT per per-execution group). ``pair_hash`` is the
    lookup key the service uses to idempotently re-attach Judge runs to an
    existing Pair. ``comparison_group_id`` deliberately lives on Result
    rows, not Pair rows: a re-evaluation that changes Judge model / prompt
    / comparison_group should NOT create a new Pair, only a new Result.
    """

    __tablename__ = "eval_trial_pairs"
    __table_args__ = (
        CheckConstraint(
            "baseline_trial_id <> candidate_trial_id",
            name="ck_eval_trial_pairs_distinct_trials",
        ),
        CheckConstraint(
            "pair_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_trial_pairs_pair_hash",
        ),
        CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_trial_pairs_input_hash",
        ),
        CheckConstraint(
            "case_id <> ''",
            name="ck_eval_trial_pairs_nonempty_keys",
        ),
        UniqueConstraint(
            "pair_hash", name="uq_eval_trial_pairs_pair_hash"
        ),
        # NON-UNIQUE composite index on the ordered (baseline, candidate)
        # trial tuple. The Pair's stable identity is ``pair_hash`` (which
        # is content-aware); the trial-tuple alone is NOT unique because a
        # re-collect that moves output bytes is allowed to create a SECOND
        # Pair row with the SAME trial ids but a DIFFERENT pair_hash. The
        # old row stays attributable to the old bytes; the new row
        # attributes the new bytes. Together with ``UNIQUE(pair_hash)``
        # this gives:
        #   * same trials + same outputs  → same pair_hash → reuse row
        #   * same trials + changed      → new pair_hash  → new Pair
        #                                  row coexists with the old one
        # This index only accelerates lookup-by-trial-tuple.
        Index(
            "ix_eval_trial_pairs_trial_ids",
            "baseline_trial_id",
            "candidate_trial_id",
        ),
        Index("ix_eval_trial_pairs_case", "case_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    baseline_trial_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("eval_trials.id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_trial_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("eval_trials.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    pair_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    allowed_evidence_kinds: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    judge_prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    judge_rubric_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class EvalPairwiseJudgeResult(Base):
    """One physical Pairwise Judge execution for one ``EvalTrialPair``.

    Winner values are constrained to {a, b, tie, both_unacceptable} and
    are NULL iff ``judge_run_status='invalid_structured_output'``. The
    ``invalid`` value is deliberately NOT a winner — invalidity lives in
    ``judge_run_status`` per the user's PR-9c.1 correction. Both display-
    side (``raw_display_winner``) and baseline-relative
    (``normalized_winner``) winners are persisted so downstream analysis
    can audit position bias without re-running the Judge.

    ``comparison_group_id`` is recorded HERE, not on the Pair: a
    re-evaluation that bumps the prompt / model or re-runs the swap
    produces a new ``comparison_group_id`` and a new Result row, but
    reuses the SAME ``EvalTrialPair`` so historical Runs aggregate
    cleanly under one Pair identity.
    """

    __tablename__ = "eval_pairwise_judge_results"
    __table_args__ = (
        CheckConstraint(
            "judge_run_status IN ('completed','invalid_structured_output')",
            name="ck_eval_pairwise_judge_results_status",
        ),
        CheckConstraint(
            "position_variant IN ('baseline','swapped')",
            name="ck_eval_pairwise_judge_results_position_variant",
        ),
        CheckConstraint(
            "raw_display_winner IS NULL OR "
            "raw_display_winner IN ('a','b','tie','both_unacceptable')",
            name="ck_eval_pairwise_judge_results_raw_winner",
        ),
        CheckConstraint(
            "normalized_winner IS NULL OR "
            "normalized_winner IN ('a','b','tie','both_unacceptable')",
            name="ck_eval_pairwise_judge_results_normalized_winner",
        ),
        CheckConstraint(
            "confidence IS NULL OR confidence IN ('low','medium','high')",
            name="ck_eval_pairwise_judge_results_confidence",
        ),
        CheckConstraint(
            "(judge_run_status='completed') = (raw_display_winner IS NOT NULL "
            "AND normalized_winner IS NOT NULL)",
            name="ck_eval_pairwise_judge_results_completed_carries_verdict",
        ),
        CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_pairwise_judge_results_input_hash",
        ),
        CheckConstraint(
            "comparison_group_id <> ''",
            name="ck_eval_pairwise_judge_results_group",
        ),
        UniqueConstraint(
            "pair_id", "judge_run_id",
            name="uq_eval_pairwise_judge_results_pair_run",
        ),
        Index("ix_eval_pairwise_judge_results_pair", "pair_id"),
        Index("ix_eval_pairwise_judge_results_run", "judge_run_id"),
        Index(
            "ix_eval_pairwise_judge_results_group",
            "comparison_group_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    pair_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("eval_trial_pairs.id", ondelete="CASCADE"),
        nullable=False,
    )
    judge_run_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    judge_run_status: Mapped[str] = mapped_column(String(32), nullable=False)
    position_variant: Mapped[str] = mapped_column(String(16), nullable=False)
    comparison_group_id: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_display_winner: Mapped[str | None] = mapped_column(String(32))
    normalized_winner: Mapped[str | None] = mapped_column(String(32))
    raw_dimension_verdicts: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    normalized_dimension_verdicts: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    confidence: Mapped[str | None] = mapped_column(String(16))
    rationale: Mapped[str | None] = mapped_column(Text)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    rubric_version: Mapped[str] = mapped_column(String(32), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_output_hash: Mapped[str | None] = mapped_column(String(64))
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    calibrated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# ===========================================================================
# PR-9c.2 Calibration persistence (alembic 20260815_0015).
#
# Four tables wrapped around PR-9c.1's two Pair/Judge tables to form the
# calibration workflow: Sweep control plane, frozen per-(pair, position)
# SweepItem work units, reviewer Annotations (raw a/b + normalized
# baseline/candidate), and versioned CalibrationReport snapshots.
#
# Key invariants enforced at ORM layer (DDL mirrors these in 0015):
# - Sweep counters separate pair_count vs judge_run_count.
# - SweepItem.judge_run_id is the deterministic uuid5 helper output.
# - Annotation raw_dimension_verdicts vocabulary: a/b/tie/both_unacceptable;
#   normalized_dimensions vocabulary: baseline/candidate/tie/both_unacceptable.
#   Stored as 10 explicit per-dim columns (Postgres forbids subqueries in
#   CHECK, so we cannot enforce JSONB integrity there).
# - CalibrationReport UNIQUE(input_hash); same input + different content
#   is an integrity violation, not a hidden second row.
# - cancel_requested_at is a STAGING FACT, not a terminal status. Only the
#   SweepExecutor (Commit 3) writes status='cancelled'.
# ===========================================================================


class EvalPairwiseSweep(Base):
    """Sweep control plane. One row per Judge sweep over a frozen Pair
    Export JSONL with both original and swapped position variants."""

    __tablename__ = "eval_pairwise_sweeps"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled')",
            name="ck_eval_pairwise_sweeps_status",
        ),
        CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_eval_pairwise_sweeps_source_sha",
        ),
        CheckConstraint(
            "requested_pair_count > 0",
            name="ck_eval_pairwise_sweeps_requested_pair_positive",
        ),
        CheckConstraint(
            "requested_judge_run_count = requested_pair_count * 2",
            name="ck_eval_pairwise_sweeps_runs_eq_pairs_times_two",
        ),
        CheckConstraint(
            "completed_judge_run_count + failed_judge_run_count "
            "<= requested_judge_run_count",
            name="ck_eval_pairwise_sweeps_terminal_le_requested",
        ),
        CheckConstraint(
            "completed_pair_count <= requested_pair_count",
            name="ck_eval_pairwise_sweeps_pairs_le_requested",
        ),
        CheckConstraint(
            "position_pair_count <= completed_pair_count",
            name="ck_eval_pairwise_sweeps_position_le_completed_pairs",
        ),
        CheckConstraint(
            "comparison_group_id <> ''",
            name="ck_eval_pairwise_sweeps_group_nonempty",
        ),
        CheckConstraint(
            "(status = 'cancelled') = (cancel_requested_at IS NOT NULL AND "
            "terminal_at IS NOT NULL)",
            name="ck_eval_pairwise_sweeps_cancelled_implies_both_timestamps",
        ),
        UniqueConstraint(
            "comparison_group_id",
            name="uq_eval_pairwise_sweeps_comparison_group",
        ),
        Index("ix_eval_pairwise_sweeps_dataset", "dataset_id", "dataset_version"),
        Index("ix_eval_pairwise_sweeps_status", "status"),
        Index(
            "ix_eval_pairwise_sweeps_experiments",
            "baseline_experiment_id",
            "candidate_experiment_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    dataset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(32), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    export_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    baseline_experiment_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("eval_experiments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    candidate_experiment_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("eval_experiments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    judge_model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    judge_prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    judge_rubric_version: Mapped[str] = mapped_column(String(32), nullable=False)
    annotation_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    comparison_group_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_pair_count: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_judge_run_count: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_judge_run_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    failed_judge_run_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    completed_pair_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    position_pair_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    error_summary: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    fixture_mapping: Mapped[dict[str, dict[str, object]] | None] = mapped_column(
        JSONB,
        comment=(
            "Optional pair_hash → raw_display_winner mapping consumed "
            "by FixturePairwiseJudge when judge_llm_provider='fixture'. "
            "MUST be NULL for production sweeps (live LLM path)."
        ),
    )
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvalPairwiseSweepItem(Base):
    """One frozen (pair, position_variant) work unit inside a Sweep.

    The deterministic ``judge_run_id`` (uuid5 of
    ``sweep_id|pair_hash|position_variant|model|prompt|rubric``) means a
    crashed Sweep recovers by querying items with status IN
    (queued, running) and replaying them — the Sampler is NEVER re-run.
    """

    __tablename__ = "eval_pairwise_sweep_items"
    __table_args__ = (
        CheckConstraint(
            "position_variant IN ('baseline','swapped')",
            name="ck_eval_pairwise_sweep_items_position",
        ),
        CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled')",
            name="ck_eval_pairwise_sweep_items_status",
        ),
        CheckConstraint(
            "pair_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_pairwise_sweep_items_pair_hash",
        ),
        CheckConstraint(
            "baseline_output_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_pairwise_sweep_items_baseline_sha",
        ),
        CheckConstraint(
            "candidate_output_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_pairwise_sweep_items_candidate_sha",
        ),
        CheckConstraint(
            "frozen_review_surface_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_eval_pairwise_sweep_items_review_surface_sha",
        ),
        CheckConstraint(
            "(position_variant = 'baseline' AND "
            "display_a_trial_id = baseline_trial_id AND "
            "display_b_trial_id = candidate_trial_id) OR "
            "(position_variant = 'swapped' AND "
            "display_a_trial_id = candidate_trial_id AND "
            "display_b_trial_id = baseline_trial_id)",
            name="ck_eval_pairwise_sweep_items_position_consistency",
        ),
        CheckConstraint(
            "(status IN ('completed','failed','cancelled')) = "
            "(terminal_at IS NOT NULL)",
            name="ck_eval_pairwise_sweep_items_terminal_status",
        ),
        CheckConstraint(
            "(status = 'completed') = (judge_result_id IS NOT NULL)",
            name="ck_eval_pairwise_sweep_items_completed_requires_result",
        ),
        UniqueConstraint(
            "sweep_id",
            "pair_id",
            "position_variant",
            name="uq_eval_pairwise_sweep_items_sweep_pair_pos",
        ),
        UniqueConstraint(
            "judge_run_id",
            name="uq_eval_pairwise_sweep_items_judge_run_id",
        ),
        Index(
            "ix_eval_pairwise_sweep_items_sweep_status", "sweep_id", "status"
        ),
        Index(
            "ix_eval_pairwise_sweep_items_pair_pos", "pair_id", "position_variant"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    sweep_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("eval_pairwise_sweeps.id", ondelete="CASCADE"),
        nullable=False,
    )
    pair_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("eval_trial_pairs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    position_variant: Mapped[str] = mapped_column(String(16), nullable=False)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    pair_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    baseline_trial_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    candidate_trial_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    baseline_output_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_output_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    display_a_trial_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    display_b_trial_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    frozen_review_surface_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    judge_run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    judge_result_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("eval_pairwise_judge_results.id", ondelete="SET NULL"),
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# The five canonical dimensions stored as explicit columns on
# EvalPairwiseHumanAnnotation (Postgres forbids subqueries in CHECK; explicit
# columns give us CHECK-level guarantees without JSONB integrity gymnastics).
_EVAL_PAIRWISE_DIMENSIONS: tuple[str, ...] = (
    "actionability",
    "alignment",
    "personalization",
    "clarity",
    "consistency",
)


class EvalPairwiseHumanAnnotation(Base):
    """One reviewer annotation (primary or adjudication) for one Pair.

    The reviewer's raw verdict uses position vocabulary (``a``/``b``) — the
    reviewer only ever sees display_a vs display_b. The normalized verdict
    is baseline-relative (``baseline``/``candidate``) and is derived from the
    raw verdict + the deterministic ``position_variant`` of the frozen
    review surface the reviewer was shown.

    Five dimensions are stored as 10 explicit columns (raw_dim_* and
    norm_dim_*). Use ``EvalPairwiseHumanAnnotationHelper`` to (de)serialize
    between dict and column views.
    """

    __tablename__ = "eval_pairwise_human_annotations"
    __table_args__ = (
        CheckConstraint(
            "reviewer_role IN ('primary','adjudicator')",
            name="ck_eval_pairwise_ann_role",
        ),
        CheckConstraint(
            "(reviewer_role = 'adjudicator') = is_adjudication",
            name="ck_eval_pairwise_ann_role_adjudication_eq",
        ),
        CheckConstraint(
            "position_variant IN ('baseline','swapped')",
            name="ck_eval_pairwise_ann_position",
        ),
        CheckConstraint(
            "raw_winner IN ('a','b','tie','both_unacceptable')",
            name="ck_eval_pairwise_ann_raw_winner",
        ),
        CheckConstraint(
            "normalized_winner IN "
            "('baseline','candidate','tie','both_unacceptable')",
            name="ck_eval_pairwise_ann_normalized_winner",
        ),
        CheckConstraint(
            "raw_dim_actionability IN ('a','b','tie','both_unacceptable')",
            name="ck_eval_pairwise_ann_raw_actionability",
        ),
        CheckConstraint(
            "raw_dim_alignment IN ('a','b','tie','both_unacceptable')",
            name="ck_eval_pairwise_ann_raw_alignment",
        ),
        CheckConstraint(
            "raw_dim_personalization IN ('a','b','tie','both_unacceptable')",
            name="ck_eval_pairwise_ann_raw_personalization",
        ),
        CheckConstraint(
            "raw_dim_clarity IN ('a','b','tie','both_unacceptable')",
            name="ck_eval_pairwise_ann_raw_clarity",
        ),
        CheckConstraint(
            "raw_dim_consistency IN ('a','b','tie','both_unacceptable')",
            name="ck_eval_pairwise_ann_raw_consistency",
        ),
        CheckConstraint(
            "norm_dim_actionability IN "
            "('baseline','candidate','tie','both_unacceptable')",
            name="ck_eval_pairwise_ann_norm_actionability",
        ),
        CheckConstraint(
            "norm_dim_alignment IN "
            "('baseline','candidate','tie','both_unacceptable')",
            name="ck_eval_pairwise_ann_norm_alignment",
        ),
        CheckConstraint(
            "norm_dim_personalization IN "
            "('baseline','candidate','tie','both_unacceptable')",
            name="ck_eval_pairwise_ann_norm_personalization",
        ),
        CheckConstraint(
            "norm_dim_clarity IN "
            "('baseline','candidate','tie','both_unacceptable')",
            name="ck_eval_pairwise_ann_norm_clarity",
        ),
        CheckConstraint(
            "norm_dim_consistency IN "
            "('baseline','candidate','tie','both_unacceptable')",
            name="ck_eval_pairwise_ann_norm_consistency",
        ),
        CheckConstraint(
            "review_input_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_pairwise_ann_review_input_sha",
        ),
        CheckConstraint(
            "submission_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_pairwise_ann_submission_sha",
        ),
        CheckConstraint(
            "frozen_review_surface_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_eval_pairwise_ann_review_surface_sha",
        ),
        UniqueConstraint(
            "dataset_id",
            "pair_id",
            "reviewer_id",
            "review_input_hash",
            name="uq_eval_pairwise_ann_dataset_pair_reviewer_surface",
        ),
        Index("ix_eval_pairwise_ann_pair", "pair_id"),
        Index("ix_eval_pairwise_ann_sweep", "sweep_id"),
        Index("ix_eval_pairwise_ann_reviewer", "reviewer_id"),
        Index("ix_eval_pairwise_ann_dataset", "dataset_id", "dataset_version"),
        Index(
            "uq_eval_pairwise_ann_adjudication",
            "pair_id",
            "review_input_hash",
            unique=True,
            postgresql_where=text("is_adjudication = TRUE"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    dataset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(32), nullable=False)
    sweep_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("eval_pairwise_sweeps.id", ondelete="RESTRICT"),
        nullable=False,
    )
    pair_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("eval_trial_pairs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reviewer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reviewer_role: Mapped[str] = mapped_column(String(16), nullable=False)
    is_adjudication: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    annotation_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    rubric_version: Mapped[str] = mapped_column(String(32), nullable=False)
    judge_prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    judge_model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    frozen_review_surface_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    position_variant: Mapped[str] = mapped_column(String(16), nullable=False)
    display_a_trial_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    display_b_trial_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    raw_winner: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_dim_actionability: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_dim_alignment: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_dim_personalization: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_dim_clarity: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_dim_consistency: Mapped[str] = mapped_column(String(32), nullable=False)
    normalized_winner: Mapped[str] = mapped_column(String(32), nullable=False)
    norm_dim_actionability: Mapped[str] = mapped_column(String(32), nullable=False)
    norm_dim_alignment: Mapped[str] = mapped_column(String(32), nullable=False)
    norm_dim_personalization: Mapped[str] = mapped_column(String(32), nullable=False)
    norm_dim_clarity: Mapped[str] = mapped_column(String(32), nullable=False)
    norm_dim_consistency: Mapped[str] = mapped_column(String(32), nullable=False)
    review_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    submission_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    # ---- dict-view helpers (Pydantic boundary) ----
    #
    # Service / HTTP code prefers dict views. The 10 explicit columns are
    # the persistence form; these helpers convert between the two without
    # going through JSONB. Dimensions are always returned in
    # ``_EVAL_PAIRWISE_DIMENSIONS`` order.

    def raw_dimension_verdicts(self) -> dict[str, str]:
        return {
            dim: getattr(self, f"raw_dim_{dim}")
            for dim in _EVAL_PAIRWISE_DIMENSIONS
        }

    def normalized_dimension_verdicts(self) -> dict[str, str]:
        return {
            dim: getattr(self, f"norm_dim_{dim}")
            for dim in _EVAL_PAIRWISE_DIMENSIONS
        }


class EvalPairwiseCalibrationReport(Base):
    """Versioned calibration report snapshot.

    ``input_hash`` is the report's identity (UNIQUE); same input + different
    ``content_hash`` is an integrity violation flagged by the Service layer
    (DB UNIQUE on input_hash prevents a silent second row). Reports are
    written explicitly via POST ``/pairwise/calibration``; GET endpoints
    only read existing rows.
    """

    __tablename__ = "eval_pairwise_calibration_reports"
    __table_args__ = (
        CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_eval_pairwise_reports_source_sha",
        ),
        CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_pairwise_reports_input_sha",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_pairwise_reports_content_sha",
        ),
        CheckConstraint(
            "dataset_version <> ''",
            name="ck_eval_pairwise_reports_version_nonempty",
        ),
        UniqueConstraint(
            "input_hash", name="uq_eval_pairwise_reports_input_hash"
        ),
        Index(
            "ix_eval_pairwise_reports_dataset_created",
            "dataset_id",
            "dataset_version",
            "created_at",
        ),
        Index("ix_eval_pairwise_reports_judge", "judge_model_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    dataset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(32), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    judge_model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    judge_prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    judge_rubric_version: Mapped[str] = mapped_column(String(32), nullable=False)
    annotation_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    calibration_policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    report_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
