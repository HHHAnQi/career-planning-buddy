"""Doc summary table must equal the artifact — no hand-maintained numbers."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))

from generate_context_report_table import render  # noqa: E402

DOC = BACKEND.parent / "docs/evals/context-compression-v1.md"
REPORT = BACKEND / "evals/artifacts/context-compression-v3-report.json"


def test_doc_table_matches_artifact() -> None:
    expected = render(REPORT)
    text = DOC.read_text(encoding="utf-8")
    m = re.search(
        r"<!-- AUTO-TABLE:BEGIN -->\n(.*?)\n<!-- AUTO-TABLE:END -->",
        text,
        re.S,
    )
    assert m, "auto-table markers missing from doc"
    actual = m.group(1).strip()
    assert actual == expected.strip(), (
        f"doc table drifted from artifact:\n--- doc ---\n{actual}\n"
        f"--- artifact ---\n{expected}"
    )


def test_artifact_denominators_are_consistent() -> None:
    data = json.loads(REPORT.read_text(encoding="utf-8"))
    for strategy in ("full", "recent", "relevant_summary"):
        block = data["summary"][strategy]
        for subset in ("all_samples", "sendable_samples"):
            micro = block[subset]["fact_retention_micro"]
            total = (
                micro["retained"] + micro["needs_review"] + micro["lost"]
            )
            assert total == micro["total"], (strategy, subset, micro)
