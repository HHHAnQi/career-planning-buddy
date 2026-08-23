"""Run the rubric judge over the annotation worksheet.

Reads ``evals/annotations/rubric-v1-worksheet.jsonl``, scores every row
with the selected judge, and writes
``evals/annotations/rubric-v1-judge-scores.jsonl``.

Modes:

* ``deterministic`` (default) — the two verifiable dimensions only; free,
  offline, CI-safe.
* ``llm`` — all four dimensions on the independent judge model
  (``JUDGE_LLM_*`` settings); judge and judged model must come from
  different families (self-preference bias).

Usage::

    python -m scripts.run_rubric_judge --judge deterministic
    python -m scripts.run_rubric_judge --judge llm
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings
from app.schemas.agent_runs import PlanCandidate
from evals.v2.rubric_judge import (
    RUBRIC_JUDGE_PROMPT_VERSION,
    RubricJudgeInput,
    build_rubric_judge,
)

WORKSHEET_PATH = Path("evals/annotations/rubric-v1-worksheet.jsonl")
SCORES_PATH = Path("evals/annotations/rubric-v1-judge-scores.jsonl")


async def run(judge_mode: str, rows: list[dict[str, object]]) -> list[dict[str, object]]:
    settings = get_settings()
    judge = build_rubric_judge(settings, judge_mode=judge_mode)  # type: ignore[arg-type]
    outputs: list[dict[str, object]] = []
    for row in rows:
        prompt = RubricJudgeInput(
            request_message=str(row["request_message"]),
            profile_summary=str(row["profile_summary"]),
            time_budget_minutes=int(row["time_budget_minutes"]),
            evidence_catalog_ids=list(row.get("evidence_catalog_ids", [])),
            candidate=PlanCandidate.model_validate(row["candidate"]),
        )
        scores = await judge.score(prompt)
        outputs.append(
            {
                "case_id": row["case_id"],
                "judge": judge_mode,
                "prompt_version": RUBRIC_JUDGE_PROMPT_VERSION,
                "scores": scores.model_dump(mode="json"),
                "judged_at": datetime.now(UTC).isoformat(),
            }
        )
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--judge", choices=["deterministic", "llm"], default="deterministic")
    judge_mode = parser.parse_args().judge
    rows = [
        json.loads(line)
        for line in WORKSHEET_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    outputs = asyncio.run(run(judge_mode, rows))
    SCORES_PATH.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in outputs),
        encoding="utf-8",
    )
    print(f"wrote {len(outputs)} judge scores to {SCORES_PATH} (judge={judge_mode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
