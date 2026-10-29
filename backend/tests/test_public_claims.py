from __future__ import annotations

import json
from pathlib import Path

from scripts.audit_public_claims import audit_public_claims, load_public_metrics


def _write_fixture(root: Path) -> None:
    summary = root / "backend/evals/releases/v0.3-hardgate-88.9/summary.json"
    summary.parent.mkdir(parents=True)
    summary.write_text(
        json.dumps(
            {
                "hard_gate_rate": 0.8889,
                "latency_p95_ms": 28_862,
                "trials_completed": 90,
            }
        ),
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "**88.9%** **28.9s** 90 次匿名 Trial v0.3-hardgate-88.9",
        encoding="utf-8",
    )
    current_system = root / "docs/architecture/current-system-overview.md"
    current_system.parent.mkdir(parents=True)
    current_system.write_text("Status: CURRENT", encoding="utf-8")
    (root / "RELEASE_TRUTH_AUDIT.md").write_text(
        "Status: HISTORICAL\nSee current-system-overview.md",
        encoding="utf-8",
    )


def test_load_public_metrics_formats_frozen_summary(tmp_path: Path) -> None:
    _write_fixture(tmp_path)

    metrics = load_public_metrics(tmp_path)

    assert metrics.hard_gate_rate_percent == "88.9%"
    assert metrics.latency_p95_seconds == "28.9s"
    assert metrics.trial_count == "90"


def test_audit_public_claims_accepts_evidence_backed_readme(tmp_path: Path) -> None:
    _write_fixture(tmp_path)

    assert audit_public_claims(tmp_path) == []


def test_audit_public_claims_rejects_drift_and_volatile_counts(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    (tmp_path / "README.md").write_text(
        "**87.0%** **31.0s** 814 项后端 + 36 项前端",
        encoding="utf-8",
    )

    errors = audit_public_claims(tmp_path)

    assert any("hard-gate rate" in error for error in errors)
    assert any("P95 latency" in error for error in errors)
    assert any("test counts" in error for error in errors)
