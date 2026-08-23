"""Export the rubric worksheet to an annotation-friendly CSV.

The JSONL worksheet stays the single source of truth; this export is the
human-facing editing surface: one row per case with a readable plan
digest and empty annotation columns. Open it in Excel/WPS (UTF-8 BOM),
fill the six annotation columns, then merge back with
``scripts.import_rubric_annotations``.

Usage::

    python -m scripts.export_rubric_worksheet   # → evals/annotations/…-worksheet.csv
    python -m scripts.export_rubric_worksheet --out my.csv
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

DEFAULT_OUT = Path("evals/annotations/rubric-v1-worksheet.csv")
WORKSHEET_PATH = Path("evals/annotations/rubric-v1-worksheet.jsonl")

COLUMNS = [
    "case_id",
    "请求",
    "画像",
    "时间预算(分/天)",
    "规划摘要",
    "规划理由",
    "任务速览(日期|标题|动作|交付物|分钟)",
    "证据引用数",
    "D1_目标对齐(1-5)",
    "D2_证据支撑(1-5)",
    "D3_可执行性(1-5)",
    "D4_周期合规(1-5)",
    "理由(必填,>=15字)",
    "标注人",
    "标注日期",
]


def _digest(candidate: dict[str, object]) -> str:
    raw_tasks = candidate.get("tasks", [])
    raw_focus = candidate.get("weekly_focus", [])
    tasks_list: list[dict[str, object]] = (
        raw_tasks if isinstance(raw_tasks, list) else []
    )
    focus_list: list[dict[str, object]] = (
        raw_focus if isinstance(raw_focus, list) else []
    )
    tasks = "\n".join(
        f"{task['scheduled_date']} | {task['title']} | {task['starter_action']}"
        f" | 交付:{task['deliverable']} | {task['estimated_minutes']}min"
        for task in tasks_list
    )
    focus = "\n".join(
        f"第{item.get('week_index')}周焦点: {item.get('focus')}" for item in focus_list
    )
    return f"【周焦点】\n{focus}\n【任务】\n{tasks}"


def export(out_path: Path) -> int:
    rows = [
        json.loads(line)
        for line in WORKSHEET_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    with out_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for row in rows:
            annotations = row.get("annotations")
            filled = annotations if isinstance(annotations, dict) else {}
            writer.writerow(
                [
                    row["case_id"],
                    row["request_message"],
                    row["profile_summary"],
                    row["time_budget_minutes"],
                    row["candidate"].get("summary", ""),
                    row["candidate"].get("rationale", ""),
                    _digest(row["candidate"]),
                    len(row["candidate"].get("evidence_refs", [])),
                    filled.get("goal_alignment", ""),
                    filled.get("evidence_grounding", ""),
                    filled.get("executability", ""),
                    filled.get("horizon_compliance", ""),
                    filled.get("rationale", ""),
                    filled.get("annotator", ""),
                    filled.get("annotated_at", ""),
                ]
            )
    print(f"wrote {len(rows)} rows to {out_path} (open with Excel/WPS, UTF-8 BOM)")
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    export(parser.parse_args().out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
