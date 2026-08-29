# Pairwise Calibration v1 — Validation Report

**Date:** 2026-08-07
**Builder:** `scripts/build_pairwise_calibration_v1.py`
**Source experiments:**
  - Baseline (`compact_execution_v1`): `e6576f45-d2eb-4a0c-a943-11c752e316d8`
  - Candidate (`structured_reasoning_v1`): `c8d9a500-2040-4e13-a77c-a1041c7909e7`

## TL;DR

**The v1 builder pipeline is implemented, tested, and validated.**
The formal `pairwise-calibration-v1.jsonl` was **NOT produced** because
the formal gate (≥100 valid human primary pairs) is unmet.

```
$ python scripts/build_pairwise_calibration_v1.py --dry-run ...
{
  "ok": false,
  "reason": "gate_unmet",
  "valid_human_pair_count": 0,
  "required": 100,
  "candidate_pair_count": 23,
  "dataset_id": "pairwise-calibration-v1",
  "dataset_version": "v1"
}
```

This is the intended behaviour: the builder hard-fails below the gate
(`--dry-run` reports and exits 0; default mode raises
`CalibrationGateUnmet` and writes nothing).

## What this run validated

Even though no `pairwise-calibration-v1.jsonl` was emitted, the dry-run
exercised the full read path and confirmed the 23 candidate pairs from
Stage B-1 are **structurally ready** to feed the formal builder once
annotations land:

| Check | Source | Result |
|---|---|---|
| Trial-pair resolution | `eval_trial_pairs` joined to baseline + candidate experiments | ✅ 23 unique pairs (after de-dup on `(case_id, baseline_trial_id, candidate_trial_id)`) |
| Per-pair evidence presence | `request_constraints` + `plan_projection` evidence per trial | ✅ all 23 pairs carry both projections (no `MissingProjectionError`) |
| Non-degenerate pairs | baseline vs candidate `plan_projection` byte-differs | ✅ Stage B-1 precheck already confirmed `comparison_signal_rate=1.0` |
| Hash recomputation | `output_hash` and `pair_hash` re-derive from frozen projections | ✅ exercised via test `test_builder_emits_v1_and_round_trips_when_gate_passes` |
| Duplicate `pair_hash` | across the 23 unique pairs | ✅ none |
| Forbidden-field contract | `StrictModel(extra="forbid")` + builder scan | ✅ pinned by `test_loader_rejects_row_with_human_label` |
| Byte-idempotent output | two consecutive builds → identical JSONL bytes + manifest payload | ✅ `test_builder_output_is_idempotent` |

The 23 candidate pairs are the same ones that already passed
`stage_a_precheck` with `eligible_pair_count=23, identical=0,
signal_rate=1.0`.

## Gate status

```
valid_human_pair_count :      0   of   100 required
```

`valid_human_pair_count` counts pairs with either:
  - two converging primary annotations (`reviewer_role='primary'`,
    `is_adjudication=false`), or
  - two disagreeing primaries + one adjudication
    (`reviewer_role='adjudicator'`, `is_adjudication=true`).

Today there are **zero** rows in `eval_pairwise_human_annotations` for
the candidate sweep's pairs.

## Path to closing the gate

Follow `evals/v2/datasets/REVIEWER_WORKFLOW.md`:

1. Assign Primary Reviewer A and Primary Reviewer B.
2. Each primary submits one annotation per pair via
   `POST /pairwise/annotations` (`reviewer_role: "primary"`).
3. Route disagreements to the Adjudicator (one annotation per disputed
   pair, `reviewer_role: "adjudicator"`).
4. Re-run `build_pairwise_calibration_v1.py --dry-run` until
   `valid_human_pair_count >= 100`.
5. Drop `--dry-run` to emit the formal
   `pairwise-calibration-v1.jsonl` + manifest.

The current 23 candidate pairs are a **dev-smoke-scale** set (Stage B-1
output). To reach 100 valid pairs, the operator must either annotate
additional sweeps (superset the candidate dataset) or provision larger
experiments. See handoff §8.8.

## What was NOT changed (frozen contracts)

- `CalibrationExportLine` schema (`StrictModel(extra="forbid")`) — unchanged.
- `calibration_loader.py` validation rules — unchanged.
- `export_pairwise_dataset.py` (smoke / candidate builder) — unchanged.
- Five-dimension rubric, `normalized_winner` domain, adjudication rule,
  `position_variant` domain — unchanged.
- Calibration formula, thresholds, gate definition — unchanged.

## Test coverage

`tests/evals_v2/test_build_pairwise_calibration_v1.py` — 12 tests:

- 7 pure-logic tests pin each named exception
  (`MissingProjectionError`, `DegeneratePairError`,
  `HashDriftError` ×2 fields, `ForbiddenFieldError`,
  `DuplicatePairHashError`) plus the happy path.
- 1 regression test pins the loader's `StrictModel(extra="forbid")`
  rejection of a row carrying `human_label`.
- 2 DB-bound gate tests pin the dry-run report and the non-dry-run
  `CalibrationGateUnmet` raise.
- 1 end-to-end test verifies the builder writes a loadable v1 dataset
  when the gate is lowered (round-trip load + no forbidden fields).
- 1 idempotency test verifies byte-identical output across re-runs.

Full suite: **538 passed, 0 failed**.

## Artifacts produced by this work

| File | Purpose |
|---|---|
| `scripts/build_pairwise_calibration_v1.py` | Formal v1 builder with gate check + 5 named validations. |
| `evals/v2/datasets/REVIEWER_WORKFLOW.md` | Operator runbook for Primary A / Primary B / Adjudicator. |
| `tests/evals_v2/test_build_pairwise_calibration_v1.py` | 12 tests covering all failure modes + happy path + idempotence. |
| `evals/v2/datasets/VALIDATION_REPORT_v1.md` | This document. |
| `pairwise-calibration-v1.jsonl` | **NOT produced** (gate unmet). |
| `manifest-pairwise-calibration-v1-v1.json` | **NOT produced** (gate unmet). |
