"""Calibrate the rubric judge against human annotations.

Compares the human-annotated worksheet with the judge score file,
per dimension and overall:

* Cohen's kappa on good/bad bands (≥4 = good)
* Spearman rho on raw 1–5 scores
* raw band agreement

Gate (docs/standards/plan-quality-rubric.md): kappa ≥ 0.6 AND
rho ≥ 0.75 AND agreement ≥ 0.80 → "calibrated"; otherwise
"diagnostic_only". Only dimensions scored by BOTH sides are compared —
the deterministic judge scores two of four by design.

Usage::

    python -m scripts.calibrate_rubric_judge
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from evals.v2.agreement import (
    AGREEMENT_GATE,
    KAPPA_GATE,
    SPEARMAN_GATE,
    banded_agreement,
    calibration_verdict,
    cohens_kappa,
    spearman_rho,
)
from evals.v2.rubric_judge import DIMENSIONS

WORKSHEET_PATH = Path("evals/annotations/rubric-v1-worksheet.jsonl")
SCORES_PATH = Path("evals/annotations/rubric-v1-judge-scores.jsonl")


def main() -> int:
    worksheet = {
        row["case_id"]: row
        for row in (
            json.loads(line)
            for line in WORKSHEET_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    judged = {
        row["case_id"]: row
        for row in (
            json.loads(line)
            for line in SCORES_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }

    annotated = {
        case_id: row
        for case_id, row in worksheet.items()
        if isinstance(row.get("annotations"), dict)
        and row["annotations"].get("rationale")
    }
    if len(annotated) < 10:
        raise SystemExit(
            f"only {len(annotated)} annotated rows — calibration needs the "
            "human golden labels (see docs/standards/plan-quality-rubric.md)"
        )

    report: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "annotated_rows": len(annotated),
        "judge_rows": len(judged),
        "gates": {"kappa": KAPPA_GATE, "spearman": SPEARMAN_GATE, "agreement": AGREEMENT_GATE},
        "dimensions": {},
    }
    for dimension in DIMENSIONS:
        human_scores: list[int] = []
        judge_scores: list[int] = []
        for case_id, row in annotated.items():
            human_value = row["annotations"].get(dimension)
            judge_row = judged.get(case_id)
            judge_value = (
                judge_row["scores"].get(dimension) if judge_row is not None else None
            )
            if isinstance(human_value, int) and isinstance(judge_value, int):
                human_scores.append(human_value)
                judge_scores.append(judge_value)
        if len(human_scores) < 10:
            report["dimensions"][dimension] = {
                "pairs": len(human_scores),
                "status": "skipped_insufficient_pairs",
            }
            continue
        kappa = cohens_kappa(
            ["good" if score >= 4 else "bad" for score in human_scores],
            ["good" if score >= 4 else "bad" for score in judge_scores],
        )
        rho = spearman_rho(human_scores, judge_scores)
        agreement = banded_agreement(human_scores, judge_scores)
        report["dimensions"][dimension] = {
            "pairs": len(human_scores),
            "cohens_kappa": round(kappa, 4),
            "spearman_rho": round(rho, 4),
            "band_agreement": round(agreement, 4),
            "human_mean": round(sum(human_scores) / len(human_scores), 2),
            "judge_mean": round(sum(judge_scores) / len(judge_scores), 2),
            "verdict": calibration_verdict(
                kappa=kappa, rho=rho, agreement=agreement
            ),
        }

    scored = [
        entry for entry in report["dimensions"].values()
        if isinstance(entry, dict) and "verdict" in entry
    ]
    report["overall_verdict"] = (
        "calibrated"
        if scored and all(entry["verdict"] == "calibrated" for entry in scored)
        else "diagnostic_only"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
