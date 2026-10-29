"""Audit public README claims against checked-in release evidence.

The README is a product entry point, not an independently maintained metrics
database.  Current quantitative claims must therefore be derived from a
versioned evidence package.  Historical comparisons may remain in prose only
when they are explicitly labelled as historical.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

RELEASE_NAME = "v0.3-hardgate-88.9"
SUMMARY_PATH = Path("backend/evals/releases") / RELEASE_NAME / "summary.json"
README_PATH = Path("README.md")
CURRENT_SYSTEM_PATH = Path("docs/architecture/current-system-overview.md")
TRUTH_AUDIT_PATH = Path("RELEASE_TRUTH_AUDIT.md")


@dataclass(frozen=True)
class PublicMetrics:
    hard_gate_rate_percent: str
    latency_p95_seconds: str
    trial_count: str


def load_public_metrics(project_root: Path = PROJECT_ROOT) -> PublicMetrics:
    """Load the public headline metrics from the frozen release summary."""
    payload = json.loads((project_root / SUMMARY_PATH).read_text(encoding="utf-8"))
    return PublicMetrics(
        hard_gate_rate_percent=f"{float(payload['hard_gate_rate']) * 100:.1f}%",
        latency_p95_seconds=f"{int(payload['latency_p95_ms']) / 1000:.1f}s",
        trial_count=str(int(payload["trials_completed"])),
    )


def audit_public_claims(project_root: Path = PROJECT_ROOT) -> list[str]:
    """Return stable errors when public claims drift from repository evidence."""
    metrics = load_public_metrics(project_root)
    readme = (project_root / README_PATH).read_text(encoding="utf-8")
    current_system = (project_root / CURRENT_SYSTEM_PATH).read_text(encoding="utf-8")
    truth_audit = (project_root / TRUTH_AUDIT_PATH).read_text(encoding="utf-8")
    errors: list[str] = []

    required_readme_fragments = {
        f"**{metrics.hard_gate_rate_percent}**": "hard-gate rate",
        f"**{metrics.latency_p95_seconds}**": "P95 latency",
        f"{metrics.trial_count} 次匿名 Trial": "trial count",
        RELEASE_NAME: "release evidence link",
    }
    for fragment, label in required_readme_fragments.items():
        if fragment not in readme:
            errors.append(f"README {label} does not match {SUMMARY_PATH}: missing {fragment!r}")

    if re.search(r"\d+\s*项后端\s*\+\s*\d+\s*项前端", readme):
        errors.append("README must not hard-code volatile backend/frontend test counts")

    if "Status: CURRENT" not in current_system:
        errors.append(f"{CURRENT_SYSTEM_PATH} must declare Status: CURRENT")
    if "Status: HISTORICAL" not in truth_audit:
        errors.append(f"{TRUTH_AUDIT_PATH} must declare Status: HISTORICAL")
    if "current-system-overview.md" not in truth_audit:
        errors.append(f"{TRUTH_AUDIT_PATH} must link readers to the current system facts")
    return errors


def main() -> None:
    errors = audit_public_claims()
    if errors:
        raise SystemExit("\n".join(errors))
    print("public claims audit passed")


if __name__ == "__main__":
    main()
