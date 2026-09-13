"""PR-9c.2 Pairwise Pair Seed enumeration (offline sampler utility).

The sampler is an **offline tool** used to produce a frozen Export JSONL
that downstream Sweeps consume verbatim. It is NOT invoked by the Sweep
runtime: per the user's PR-9c.2 supplementary constraint #2, a Sweep must
strictly consume the immutable Export JSONL and re-validate ``pair_hash``
on the way in; it must NOT re-run the sampler during recovery.

The sampler's contract:

* Pure function (no IO);
* Same case_id pairing only — a Pair is meaningless across cases;
* Filters to trials whose state carries ``has_evidence=True`` (the Judge
  can only compare what has been collected and graded);
* Outputs a list of :class:`PairExportRecord` whose ``pair_hash`` is
  computed with the production PR-9c.1 formula. The exporter writes a
  manifest with ``source_sha256`` so downstream loaders can validate.

If a baseline/candidate trial pair does not yield equal ``pair_hash``
across a re-collect (the bytes shifted), the sampler emits a new export
line; the old export stays attributable to the old bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from evals.v2.pairwise import (
    Pair,
    PositionVariant,  # noqa: F401  (re-exported for callers wiring tests)
    TrialEvidenceProjection,
)

SAMPLER_VERSION = "v1"
EXPORT_SCHEMA_VERSION = "pairwise-calibration-export/v1"


@dataclass(frozen=True, slots=True)
class TrialBrief:
    """Slim, immutable view of a Trial used for Pair candidate enumeration.

    ``baseline_output_hash`` / ``candidate_output_hash`` are pre-computed
    ``canonical_sha256(projection.as_display())`` hashes; the sampler does
    not re-derive them, it just packages them on the export record so the
    downstream loader can independently recompute ``pair_hash``.
    """

    trial_id: UUID
    case_id: str
    status: str
    has_evidence: bool
    request_constraints: dict[str, object] | None
    plan_projection: dict[str, object] | None


@dataclass(frozen=True, slots=True)
class PairExportRecord:
    """One serialized Pair row for the Export JSONL.

    Every field non-secret + reproducible. ``pair_hash`` is the production
    sha256 over (schema_version='eval-trial-pair/v1', case_id, trial refs,
    baseline_output_hash, candidate_output_hash); the loader MUST recompute
    this and reject any line whose value differs.
    """

    schema_version: str
    pair_external_id: str
    case_id: str
    baseline_trial_id: UUID
    candidate_trial_id: UUID
    baseline_output_hash: str
    candidate_output_hash: str
    pair_hash: str
    suggested_label: str | None


def _projection(brief: TrialBrief) -> TrialEvidenceProjection:
    return TrialEvidenceProjection(
        request_constraints=brief.request_constraints,
        plan_projection=brief.plan_projection,
    )


def _synthetic_pair_hash(
    *,
    case_id: str,
    baseline_trial_id: UUID,
    candidate_trial_id: UUID,
    baseline_output_hash: str,
    candidate_output_hash: str,
) -> str:
    """Re-derive production ``Pair.pair_hash()`` without constructing a Pair.

    Why a parallel function instead of ``Pair(...).pair_hash()``? The
    sampler never needs the full :class:`Pair` graph; computing the hash
    directly avoids materializing projections twice and keeps the function
    a pure data-in/dict-out transform.
    """

    from evals.v2.contracts import canonical_sha256

    return canonical_sha256({
        "schema_version": "eval-trial-pair/v1",
        "case_id": case_id,
        "baseline_trial_id": str(baseline_trial_id),
        "candidate_trial_id": str(candidate_trial_id),
        "baseline_output_hash": baseline_output_hash,
        "candidate_output_hash": candidate_output_hash,
    })


def enumerate_pair_candidates(
    *,
    baseline_experiment_id: UUID,
    candidate_experiment_id: UUID,
    trials_baseline: list[TrialBrief],
    trials_candidate: list[TrialBrief],
    suggested_label_provider: dict[str, str] | None = None,
) -> list[PairExportRecord]:
    """Cartesian-join baseline×candidate trials within each ``case_id``.

    Rules:

    * Both Trials MUST be ``status='completed'`` (no point comparing a
      pending or failed Trial) and ``has_evidence=True`` (the Judge needs
      PLAN_PROJECTION / REQUEST_CONSTRAINTS to compare);
    * Same case_id only;
    * Baseline trial must come from ``trials_baseline``; candidate from
      ``trials_candidate``;
    * The function returns deterministic order: sort by
      ``(case_id, baseline_trial_id, candidate_trial_id)`` so the export
      bytes are reproducible;
    * We DO enforce baseline_trial_id != candidate_trial_id, but in
      practice each side comes from a different experiment so this is a
      belt-and-braces guard;
    * Output hashes: re-derived from the projection payloads via the PR-9c.1
      ``Pair.baseline_output_hash`` / ``candidate_output_hash`` formula
      (i.e. ``canonical_sha256(projection.as_display())``);
    * The function does NOT call any IO or any sampler side-effects;
    * ``suggested_label_provider`` is an optional map keyed by the
      deterministic ``pair_external_id`` ("case_id|<baseline>|<candidate>").
      It is ONLY used to pre-populate an optional helper label; per
      invariant #6 it is never returned to the reviewer APIs and never
      participates in metrics.
    """

    eligible_baseline = _eligible_trials(trials_baseline)
    eligible_candidate = _eligible_trials(trials_candidate)

    by_case_baseline = _group_by_case(eligible_baseline)
    by_case_candidate = _group_by_case(eligible_candidate)

    shared_cases = sorted(set(by_case_baseline) & set(by_case_candidate))
    records: list[PairExportRecord] = []
    for case_id in shared_cases:
        b_trials = by_case_baseline[case_id]
        c_trials = by_case_candidate[case_id]
        for b_idx, baseline_brief in enumerate(b_trials):
            for c_idx, candidate_brief in enumerate(c_trials):
                if baseline_brief.trial_id == candidate_brief.trial_id:
                    continue
                baseline_projection = _projection(baseline_brief)
                candidate_projection = _projection(candidate_brief)
                baseline_output_hash = _output_hash(baseline_projection)
                candidate_output_hash = _output_hash(candidate_projection)
                pair_hash = _synthetic_pair_hash(
                    case_id=case_id,
                    baseline_trial_id=baseline_brief.trial_id,
                    candidate_trial_id=candidate_brief.trial_id,
                    baseline_output_hash=baseline_output_hash,
                    candidate_output_hash=candidate_output_hash,
                )
                pair_external_id = (
                    f"{case_id}|{baseline_brief.trial_id}|{candidate_brief.trial_id}|"
                    f"{b_idx}|{c_idx}"
                )
                suggested = None
                if suggested_label_provider is not None:
                    suggested = suggested_label_provider.get(pair_external_id)
                records.append(
                    PairExportRecord(
                        schema_version=EXPORT_SCHEMA_VERSION,
                        pair_external_id=pair_external_id,
                        case_id=case_id,
                        baseline_trial_id=baseline_brief.trial_id,
                        candidate_trial_id=candidate_brief.trial_id,
                        baseline_output_hash=baseline_output_hash,
                        candidate_output_hash=candidate_output_hash,
                        pair_hash=pair_hash,
                        suggested_label=suggested,
                    )
                )

    records.sort(key=lambda r: (r.case_id, str(r.baseline_trial_id), str(r.candidate_trial_id)))
    return records


def _eligible_trials(trials: list[TrialBrief]) -> list[TrialBrief]:
    return [t for t in trials if t.status == "completed" and t.has_evidence]


def _group_by_case(trials: list[TrialBrief]) -> dict[str, list[TrialBrief]]:
    grouped: dict[str, list[TrialBrief]] = {}
    for t in trials:
        grouped.setdefault(t.case_id, []).append(t)
    for case in grouped:
        grouped[case].sort(key=lambda b: str(b.trial_id))
    return grouped


def _output_hash(projection: TrialEvidenceProjection) -> str:
    from evals.v2.contracts import canonical_sha256

    return canonical_sha256(projection.as_display())


__all__ = [
    "EXPORT_SCHEMA_VERSION",
    "SAMPLER_VERSION",
    "PairExportRecord",
    "TrialBrief",
    "enumerate_pair_candidates",
]


# Suppress unused-import warning for ``Pair`` — re-exported for callers
# that construct Pair graphs from PairExportRecord during testing.
_ = Pair
