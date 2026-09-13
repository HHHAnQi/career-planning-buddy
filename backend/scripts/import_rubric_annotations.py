"""Merge annotated CSV back into the rubric worksheet (JSONL).

Validates every row before writing anything: scores must be 1–5
integers, the rationale ≥ 15 characters, and every case_id must exist in
the worksheet. Partial annotation is fine — only rows with all four
scores plus a rationale are merged; other rows keep their existing
state. The JSONL is rewritten atomically (temp file + replace).

Usage::

    python -m scripts.import_rubric_annotations                # default CSV
    python -m scripts.import_rubric_annotations --csv my.csv
    python -m scripts.import_rubric_annotations --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import tempfile
from pathlib import Path

WORKSHEET_PATH = Path("evals/annotations/rubric-v1-worksheet.jsonl")
DEFAULT_CSV = Path("evals/annotations/rubric-v1-worksheet.csv")

DIMENSION_COLUMNS = {
    "goal_alignment": "D1_目标对齐(1-5)",
    "evidence_grounding": "D2_证据支撑(1-5)",
    "executability": "D3_可执行性(1-5)",
    "horizon_compliance": "D4_周期合规(1-5)",
}


def _parse_score(value: str) -> int | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        score = int(float(value))
    except ValueError:
        return None
    return score if 1 <= score <= 5 else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()

    worksheet = [
        json.loads(line)
        for line in WORKSHEET_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_case = {row["case_id"]: row for row in worksheet}

    merged = 0
    errors: list[str] = []
    with arguments.csv.open(encoding="utf-8-sig", newline="") as handle:
        for line_number, record in enumerate(csv.DictReader(handle), start=2):
            case_id = (record.get("case_id") or "").strip()
            if not case_id:
                continue
            if case_id not in by_case:
                errors.append(f"line {line_number}: unknown case_id {case_id}")
                continue
            scores = {
                dimension: _parse_score(record.get(column, ""))
                for dimension, column in DIMENSION_COLUMNS.items()
            }
            rationale = (record.get("理由(必填,>=15字)") or "").strip()
            annotator = (record.get("标注人") or "").strip()
            annotated_at = (record.get("标注日期") or "").strip()

            if all(value is None for value in scores.values()) and not rationale:
                continue  # untouched row
            missing = [
                dimension for dimension, value in scores.items() if value is None
            ]
            if missing:
                errors.append(
                    f"line {line_number} ({case_id}): missing/invalid scores "
                    f"{missing} — partially filled rows are not merged"
                )
                continue
            if len(rationale) < 15:
                errors.append(
                    f"line {line_number} ({case_id}): rationale shorter than 15 chars"
                )
                continue
            by_case[case_id]["annotations"] = {
                **scores,
                "rationale": rationale,
                "annotator": annotator or "unknown",
                "annotated_at": annotated_at,
            }
            merged += 1

    if errors:
        print("VALIDATION FAILED — nothing written:")
        for error in errors:
            print(f"  - {error}")
        raise SystemExit(1)

    action = "would merge" if arguments.dry_run else "merged"
    print(f"{action} {merged} annotations over {len(worksheet)} worksheet rows")
    if arguments.dry_run:
        return 0

    WORKSHEET_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=str(WORKSHEET_PATH.parent), suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for row in worksheet:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.replace(temp_name, WORKSHEET_PATH)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    annotated = sum(
        1 for row in worksheet if isinstance(row.get("annotations"), dict)
    )
    print(f"worksheet now has {annotated} annotated rows (golden set)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
