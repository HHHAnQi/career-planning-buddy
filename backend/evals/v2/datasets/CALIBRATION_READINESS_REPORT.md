# Calibration Dataset Readiness Report

**Date:** 2026-08-07
**Inspector:** read-only SQL pass over `eval_experiments`, `eval_trials`,
`eval_evidence_items`, `eval_trial_pairs`, `eval_pairwise_human_annotations`,
`eval_pairwise_sweeps`.
**No labels created. No annotations fabricated. No mutations performed.**

---

## TL;DR

**Three** structurally-eligible baseline/candidate experiment pairs exist
in the DB. All three carry the same `agent_variant` contrast
(`compact_execution_v1` vs `structured_reasoning_v1`), all three produce
divergent plan projections on every shared case (`comparison_signal_rate
= 1.0`, `identical_projection_count = 0`), and **none** of them carry a
single human annotation.

The largest single-pair source yields **23 eligible pairs** — well short
of the formal v1 gate's `≥100 valid_human_pair_count`. Closing the gate
requires either (a) annotating all 3 sources and aggregating, or
(b) provisioning additional variant-backed experiments. Neither creates
labels automatically; both require real reviewer work per
`REVIEWER_WORKFLOW.md`.

---

## 1. Experiment inventory

28 total `eval_experiments` rows. Filtering to `status='completed'` with
at least one completed trial:

| Tier | Pair | Baseline exp | Candidate exp | Variants | Source |
|---|---|---|---|---|---|
| A | Stage A v0 smoke | `9952ab2e-d125-4b8d-a143-687b00c70494` | `10296efa-ed5d-42cc-9651-03a147c792f6` | `compact_execution_v1` ↔ `structured_reasoning_v1` | Stage A v0-dev-smoke provision |
| B | Stage B-1 attempt #2 | `f47134e2-1750-4a13-94d8-7caf97cab3fa` | `64a507ec-13f7-4baa-a7d9-89fae678e435` | same | Mid-session re-provision before step-projection fix |
| C | Stage B-1 final | `e6576f45-d2eb-4a0c-a943-11c752e316d8` | `c8d9a500-2040-4e13-a77c-a1041c7909e7` | same | Final Stage B-1 provision (the one backing `pairwise-calibration-v1-candidate`) |

All three pairs share the same `agent_variant` contrast — there is no
second variant pair in the DB.

The remaining 22 completed experiments are single-case debug runs (1
trial each) from earlier session diagnostics; they do not form
baseline/candidate pairs and are not usable as v1 sources.

---

## 2. Per-source readiness

| Source | Shared cases | Trials w/ plan_projection (B / C) | Identical projections | Non-identical | Signal rate |
|---|---|---|---|---|---|
| A (v0 smoke) | 21 | 1 / 1 ⚠ | 0 | 21 | 1.00 |
| B (B-1 #2) | 23 | 1 / 1 ⚠ | 0 | 23 | 1.00 |
| C (B-1 final) | 23 | 23 / 23 ✅ | 0 | 23 | 1.00 |

**⚠ Evidence-completeness gap in sources A and B.** Both report
`baseline_trials_with_plan_projection = 1` and
`candidate_trials_with_plan_projection = 1` despite having 21 / 23
shared cases — i.e. only one trial per side carries a
`plan_projection` evidence row. These experiments were graded before
the step-projection UNIQUE fix (Commit 3.8) and the re-grade loop, so
their `grade_trial` pass aborted after the first trial; the other ~20
trials per side never had evidence collected.

Source **C** was re-graded cleanly in this session — every shared case
has both projections on both sides. It is the only source currently
suitable as input to `build_pairwise_calibration_v1.py` without
re-grading.

---

## 3. Annotation state (the gate blocker)

```
total_annotations             = 0
distinct_pairs_with_annotation = 0
adjudications                  = 0
```

The DB carries a single completed `eval_pairwise_sweep`
(`4d89b0ce-…`, dataset `pairwise-calibration-v1-candidate/v1`, 23/23
pairs judged by the fixture judge with 46/46 judge-runs). The fixture
mapping is **diagnostic-only** (`winner="tie"` for every pair) — those
are not human annotations and the gate-counting logic correctly
ignores them.

`eval_pairwise_human_annotations` is empty. There is no path to a
gate-eligible v1 dataset without real reviewer work.

---

## 4. Expected valid-pair yield if annotated

Assuming reviewers annotate every shared case in a source with two
converging primaries (or adjudication), the **upper bound** on
`valid_human_pair_count` per source equals the shared-case count:

| Source | Max valid pairs (if fully annotated) |
|---|---|
| A (v0 smoke) | 21 (requires re-grading first) |
| B (B-1 #2) | 23 (requires re-grading first) |
| C (B-1 final) | 23 (ready now) |
| **Sum (all three, after re-grade)** | **67** |
| **Formal gate threshold** | **100** |

**Even annotating every shared case across all three existing sources
yields at most 67 valid pairs — still 33 short of the gate.**

Note on aggregation: the three sources share the *same* case IDs and
the *same* variant contrast. Whether they count as distinct gate-eligible
pairs depends on whether the operator chooses to treat trial-identity
(`baseline_trial_id` × `candidate_trial_id`) as the pair key — in which
case sources B and C are independent (different trial runs) and could
be aggregated — or treats case-identity as the key, in which case
they're duplicates. The builder's `pair_hash` formula already encodes
trial IDs, so aggregating B + C + A would produce distinct `pair_hash`
values and the duplicate-pair check in `build_pairwise_calibration_v1.py`
would not flag them. **Confirm with the gate operator before
aggregating.**

---

## 5. Recommended next steps (read-only; no code or labels produced)

1. **Provision more variant-backed experiments** to reach the ≥100
   shared-case target. Each `stage_b1_provision.py` run produces 23
   shared cases; **4 additional runs** (with re-grading) plus the
   existing source C would supply 5 × 23 = 115 candidate pairs — enough
   to clear the gate with margin.
2. **Re-grade sources A and B** before considering them as v1 inputs.
   The current `trials_with_plan_projection = 1` would cause
   `build_pairwise_calibration_v1.py` to raise `MissingProjectionError`
   on 20+ rows. Re-grading is the same operation already validated on
   source C in this session.
3. **Confirm aggregation policy** with the gate operator: may a single
   `pairwise-calibration-v1.jsonl` carry pair rows sourced from
   multiple underlying sweeps / experiment pairs? If yes, the builder
   accepts a single `(baseline_experiment_id, candidate_experiment_id)`
   pair today — extending it to a multi-source input list is a small
   change but **outside the scope of "identify and prepare candidate
   sources."**
4. **Begin annotation per `REVIEWER_WORKFLOW.md`** only after steps 1-3
   settle. Annotation is the long pole; starting before the source set
   is finalized risks rework.

---

## 6. What this report does NOT do

- Does **not** create any human annotation.
- Does **not** fabricate labels.
- Does **not** modify experiments, trials, evidence, or sweep rows.
- Does **not** run `build_pairwise_calibration_v1.py` in write mode
  (would hard-fail anyway — `valid_human_pair_count=0 < 100`).
- Does **not** alter the calibration formula, thresholds, annotation
  contract, or evaluator architecture.

---

## Appendix: source identifiers for operator handoff

```
# Currently ready (re-graded, full evidence, precheck PASS):
baseline_experiment_id   = e6576f45-d2eb-4a0c-a943-11c752e316d8
candidate_experiment_id  = c8d9a500-2040-4e13-a77c-a1041c7909e7
expected_max_valid_pairs = 23

# Eligible after re-grade:
baseline_experiment_id   = 9952ab2e-d125-4b8d-a143-687b00c70494
candidate_experiment_id  = 10296efa-ed5d-42cc-9651-03a147c792f6
expected_max_valid_pairs = 21

baseline_experiment_id   = f47134e2-1750-4a13-94d8-7caf97cab3fa
candidate_experiment_id  = 64a507ec-13f7-4baa-a7d9-89fae678e435
expected_max_valid_pairs = 23

# Aggregated ceiling (all three): 67 — below the formal gate (100).
# Additional provisioning required to close the gap.
```
