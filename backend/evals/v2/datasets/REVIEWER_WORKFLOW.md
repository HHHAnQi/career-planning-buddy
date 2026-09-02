# Pairwise Calibration v1 — Reviewer Workflow

This document specifies the human-review workflow for the formal
`pairwise-calibration-v1` dataset. It describes how Primary Reviewer A,
Primary Reviewer B, and the Adjudicator interact with the existing
annotation API to populate `eval_pairwise_human_annotations` (the source
of truth for human labels).

> **Nothing in this document changes annotation semantics, the
> calibration formula, thresholds, or the JSONL row contract.** It only
> documents the intended operator workflow so reviewers can produce
> gate-eligible annotations.

---

## 1. Roles

| Role | DB value (`reviewer_role`) | Responsibility |
|---|---|---|
| **Primary Reviewer A** | `primary` | Independently annotates each pair. |
| **Primary Reviewer B** | `primary` | Independently annotates each pair. Must NOT consult A's verdict first. |
| **Adjudicator** | `adjudicator` | Only invoked when A and B disagree. Final binding label. |

Two independent primaries per pair is the minimum for the calibration
gate; additional primaries are allowed but not required.

## 2. Per-pair task

Both primaries see the same frozen review surface (the same
`display_a_trial_id` / `display_b_trial_id`, same `position_variant`)
and submit:

```
POST /pairwise/annotations
{
  "pair_id":            "<uuid>",
  "reviewer_id":        "<A or B operator id>",
  "reviewer_role":      "primary",
  "is_adjudication":    false,
  "position_variant":   "<from frozen surface>",
  "display_a_trial_id": "<from frozen surface>",
  "display_b_trial_id": "<from frozen surface>",
  "raw_winner":         "a" | "b" | "tie" | "both_unacceptable",
  "raw_dim_actionability":  "a" | "b" | "tie" | "both_unacceptable",
  "raw_dim_alignment":      "a" | "b" | "tie" | "both_unacceptable",
  "raw_dim_personalization":"a" | "b" | "tie" | "both_unacceptable",
  "raw_dim_clarity":        "a" | "b" | "tie" | "both_unacceptable",
  "raw_dim_consistency":    "a" | "b" | "tie" | "both_unacceptable"
}
```

The API normalizes `raw_*` (position vocabulary `a`/`b`) into
`norm_*` (baseline vocabulary `baseline`/`candidate`) using the
`position_variant` and persists both. Reviewers never submit
`normalized_*` directly.

The endpoint contract lives in `app/api/pairwise_calibration.py` and is
**unchanged** by this workflow.

## 3. Disagreement trigger

A pair is in disagreement when Primary A and Primary B submit
**different** `normalized_winner` values. The system surfaces these
to the Adjudicator via the Review Surface API; the Adjudicator then
submits a single annotation with `reviewer_role: "adjudicator"` and
`is_adjudication: true`. The DB constraint
`ck_eval_pairwise_ann_role_adjudication_eq` enforces that
`is_adjudication=true` ⇔ `reviewer_role='adjudicator'`.

The Adjudicator's verdict is the binding gold label for that pair.

## 4. Gold label derivation

For each annotated pair, the gold label is:

| Condition | Gold label | Source |
|---|---|---|
| A and B converge on `normalized_winner` | that winner | `consensus` |
| A and B disagree + Adjudicator present | Adjudicator's `normalized_winner` | `adjudication` |
| Only one primary, or both missing | none | `insufficient` |

A pair counts toward the **valid_human_pair_count** gate only when its
gold label is `consensus` or `adjudication`.

## 5. Source of truth — DB, not JSONL

Human labels live in `eval_pairwise_human_annotations` (DB). They
**never** appear in `pairwise-calibration-v1.jsonl` — the JSONL contract
is frozen projections + hashes only. The loader's `StrictModel(extra=
"forbid")` makes a JSONL row carrying `winner` / `human_label` /
`reviewer_id` / `adjudication*` fail to parse.

`scripts/build_pairwise_calibration_v1.py` reads the DB to compute
`valid_human_pair_count`; it does **not** copy labels into the JSONL.

## 6. Gate threshold

`build_pairwise_calibration_v1.py` enforces:

```
valid_human_pair_count >= 100
```

before it will emit `pairwise-calibration-v1.jsonl`. Below the gate, the
builder:

* in default mode → raises `CalibrationGateUnmet` and writes nothing;
* in `--dry-run` mode → returns a JSON status report and writes nothing.

This is the formal PR-9 calibration gate (handoff §8.8).

## 7. Data flow

```
                       ┌─────────────────────────────────────┐
                       │ eval_pairwise_human_annotations     │
   Primary A ─────────▶│ (DB — reviewer_role='primary')      │
   Primary B ─────────▶│ (DB — reviewer_role='primary')      │
                       │ ...                                 │
   Adjudicator ───────▶│ (DB — reviewer_role='adjudicator')  │
                       └─────────────┬───────────────────────┘
                                     │ count valid pairs
                                     ▼
                       build_pairwise_calibration_v1.py
                                     │
                                     │ gate >= 100?
                       ┌─────────────┴───────────────┐
                       ▼ no                          ▼ yes
              CalibrationGateUnmet           pairwise-calibration-v1.jsonl
              (writes nothing)               + manifest (round-trip load)
                                     │
                                     ▼
                  downstream calibration report
                  (status / usage_mode from real agreement)
```

## 8. Practical reviewer runbook

1. Operator runs the Sweep on `pairwise-calibration-v1-candidate` (or a
   superset) — produces `eval_pairwise_sweep_items` + frozen review
   surfaces.
2. Operator assigns Primary A and Primary B. Each works through the
   pairs via the Review Surface UI/API, submitting one primary
   annotation per pair.
3. Disagreements are routed to the Adjudicator. Adjudicator submits one
   adjudication per disputed pair.
4. Operator runs `build_pairwise_calibration_v1.py --dry-run` to check
   the gate. Repeat 2-3 until `valid_human_pair_count >= 100`.
5. Operator runs `build_pairwise_calibration_v1.py` (no `--dry-run`).
   The formal `pairwise-calibration-v1.jsonl` is emitted and
   round-trip-validated against the loader.
6. Operator runs `stage_b1_calibration.py --sweep-id <v1-sweep>` (or
   the equivalent calibration-report driver) against the new dataset to
   produce the agreement / position_bias report.

## 9. Constraints (frozen by PR-9c.2 contracts)

* Five-dimension rubric: actionability, alignment, personalization,
  clarity, consistency. Unchanged.
* `normalized_winner` domain: `baseline | candidate | tie |
  both_unacceptable`. Unchanged.
* `position_variant` domain: `baseline | swapped`. Unchanged.
* Adjudication rule: a row with `reviewer_role='adjudicator'` must have
  `is_adjudication=true` (and vice versa) — DB CHECK constraint.
* Per-(dataset, pair, reviewer, review_input) uniqueness enforced by
  `uq_eval_pairwise_ann_dataset_pair_reviewer_surface`.

This workflow document does not relax, tighten, or otherwise modify any
of the above.
