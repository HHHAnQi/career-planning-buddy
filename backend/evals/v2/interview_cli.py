"""One-command deterministic Career Coach Batch 3 Eval report."""

import asyncio
import json

from evals.v2.interview_dataset import load_interview_dataset
from evals.v2.interview_runner import run_interview_cases


def main() -> int:
    dataset = load_interview_dataset()
    report = asyncio.run(run_interview_cases(dataset.cases))
    print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True, default=str))
    return 0 if report.deterministic and report.passed_count == report.case_count else 3


if __name__ == "__main__":
    raise SystemExit(main())
