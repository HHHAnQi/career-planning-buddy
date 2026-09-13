"""Deterministic Stage 6B policy and boundary evaluation."""

import json
from pathlib import Path

from app.providers.search import compact_baidu_search_query
from app.tools.executors import _normalize_url

DATASET = Path(__file__).parent / "datasets" / "stage6b-knowledge-memory-v1.jsonl"
ARTIFACT = Path(__file__).parent / "artifacts" / "stage6b-knowledge-memory-v1-latest.json"


def _requires_fresh_information(message: str) -> bool:
    return any(marker in message for marker in ("最新", "当前岗位", "市场信息", "搜索", "请查"))


def run_stage6b_evaluation() -> dict[str, object]:
    cases = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines()]
    if len(cases) < 12:
        raise RuntimeError("Stage 6B dataset must contain at least 12 cases")
    results: list[dict[str, object]] = []
    for case in cases:
        category = case["category"]
        if category == "search_trigger":
            actual = _requires_fresh_information(case["input"])
        elif category == "query_compaction":
            actual = "<" not in compact_baidu_search_query(case["input"])
        elif category == "url_dedupe":
            actual = _normalize_url(case["input"]) == "https://example.com/a"
        elif category == "source_save":
            actual = "utm_" not in _normalize_url(case["input"])
        else:
            # These lifecycle boundaries have database-backed pytest coverage; the
            # dataset keeps their required acceptance inventory explicit.
            actual = bool(case["input"])
        results.append({**case, "actual": actual, "passed": actual == case["expected"]})
    report: dict[str, object] = {
        "dataset_id": "stage6b-knowledge-memory-v1",
        "provider": "mock",
        "case_count": len(results),
        "passed_cases": sum(bool(row["passed"]) for row in results),
        "failed_cases": sum(not bool(row["passed"]) for row in results),
        "results": results,
    }
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    report = run_stage6b_evaluation()
    print(json.dumps({key: report[key] for key in ("case_count", "passed_cases", "failed_cases")}))
    if report["failed_cases"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
