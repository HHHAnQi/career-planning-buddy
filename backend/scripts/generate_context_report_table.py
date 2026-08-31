"""Auto-generate the context-compression summary table from the v3 JSON
artifact. The doc table must NEVER be hand-maintained — this script is
the single source, and a consistency test asserts doc == artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPORT = Path("evals/artifacts/context-compression-v3-report.json")
DOC = Path("../docs/evals/context-compression-v1.md")
BEGIN = "<!-- AUTO-TABLE:BEGIN -->"
END = "<!-- AUTO-TABLE:END -->"


def render(report_path: Path) -> str:
    data = json.loads(report_path.read_text(encoding="utf-8"))
    lines = [
        "| 策略 | 降幅（全样本） | 事实保留（全样本） | 保留（可发送样本） |",
        "|---|---|---|---|",
    ]
    for strategy in ("full", "recent", "relevant_summary"):
        block = data["summary"][strategy]
        all_s = block["all_samples"]
        send = block["sendable_samples"]
        micro_all = all_s["fact_retention_micro"]
        micro_send = send["fact_retention_micro"]
        lines.append(
            "| {name} | {red:.1%} | **{ra}/{ta}** | **{rs}/{ts}** |".format(
                name=strategy,
                red=all_s["mean_input_token_reduction"],
                ra=micro_all["retained"],
                ta=micro_all["total"],
                rs=micro_send["retained"],
                ts=micro_send["total"],
            )
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default=str(REPORT))
    parser.add_argument("--doc", default=str(DOC))
    args = parser.parse_args()
    table = render(Path(args.report))
    doc = Path(args.doc)
    text = doc.read_text(encoding="utf-8")
    if BEGIN in text and END in text:
        start = text.index(BEGIN) + len(BEGIN)
        end = text.index(END)
        text = text[:start] + "\n" + table + "\n" + text[end:]
    else:
        print(f"{doc} lacks markers; table printed to stdout instead:")
        print(table)
        return 1
    doc.write_text(text, encoding="utf-8")
    print("table updated from artifact")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
