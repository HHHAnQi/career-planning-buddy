"""Run the fixed Stage 5 Eval dataset and persist reviewable artifacts."""

import argparse
import asyncio
import json

from evals.runner import run_evaluation
from evals.stage6_runner import run_stage6_evaluation


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-persist", action="store_true")
    arguments = parser.parse_args()
    stage5 = asyncio.run(
        run_evaluation(case_limit=arguments.limit, persist=not arguments.no_persist)
    )
    stage6 = asyncio.run(run_stage6_evaluation(persist=not arguments.no_persist))
    summary = {
        "stage5": {key: value for key, value in stage5.items() if key != "results"},
        "stage6": {key: value for key, value in stage6.items() if key != "results"},
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
