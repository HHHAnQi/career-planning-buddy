"""Print the deterministic intent-router evaluation report."""

import json

from evals.intent_router import evaluate_intent_router


def main() -> None:
    report = evaluate_intent_router()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["failed_cases"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
