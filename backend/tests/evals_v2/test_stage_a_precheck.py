"""Minimal unit tests for ``scripts.stage_a_precheck`` pure metric
computation against a constructed fake-session response.

Covers the four exit-code branches without provisioning a real DB."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from scripts.stage_a_precheck import _exit_code


def _metrics(**kw: Any) -> dict[str, Any]:
    base = {
        "eligible_pair_count": 0,
        "nonidentical_projection_pair_count": 0,
        "identical_projection_pair_count": 0,
        "comparison_signal_rate": 0.0,
        "min_required": 20,
    }
    base.update(kw)
    return base


def test_exit_code_ok_signal_full() -> None:
    # Exactly the threshold, full signal.
    rc = _exit_code(
        {
            "metrics": _metrics(
                eligible_pair_count=20,
                nonidentical_projection_pair_count=20,
                identical_projection_pair_count=0,
                comparison_signal_rate=1.0,
            )
        }
    )
    assert rc == 0


def test_exit_code_ok_above_min() -> None:
    rc = _exit_code(
        {
            "metrics": _metrics(
                eligible_pair_count=23,
                nonidentical_projection_pair_count=23,
                identical_projection_pair_count=0,
                comparison_signal_rate=1.0,
            )
        }
    )
    assert rc == 0


def test_exit_code_zerodata_when_reason_set() -> None:
    rc = _exit_code(
        {
            "reason": "ZERODATA: no trials",
            "metrics": _metrics(),
        }
    )
    assert rc == 2


def test_exit_code_eligible_below_min() -> None:
    rc = _exit_code(
        {
            "metrics": _metrics(
                eligible_pair_count=10,
                nonidentical_projection_pair_count=10,
                comparison_signal_rate=1.0,
            )
        }
    )
    assert rc == 3


def test_exit_code_signal_collapse() -> None:
    # Eligible >= min, but half are identical projections → nonidentical
    # below min.
    rc = _exit_code(
        {
            "metrics": _metrics(
                eligible_pair_count=23,
                nonidentical_projection_pair_count=10,
                identical_projection_pair_count=13,
                comparison_signal_rate=10 / 23,
            )
        }
    )
    assert rc == 4


def test_exit_code_rate_below_one() -> None:
    # Eligible >= min, nonidentical >= min, but rate is not exactly 1.0
    # (one identical pair slipping through).
    rc = _exit_code(
        {
            "metrics": _metrics(
                eligible_pair_count=23,
                nonidentical_projection_pair_count=22,
                identical_projection_pair_count=1,
                comparison_signal_rate=22 / 23,
            )
        }
    )
    assert rc == 5


def test_pair_signal_types() -> None:
    # Sanity: random UUIDs make exit 2 (no trials found).
    import asyncio

    from scripts.stage_a_precheck import precheck

    rc = asyncio.run(
        precheck(
            baseline_experiment_id=uuid4(),
            candidate_experiment_id=uuid4(),
        )
    )
    assert rc["ok"] is False
    assert "ZERODATA" in rc["reason"]
    # Default-shape invariants
    for k in (
        "eligible_pair_count",
        "nonidentical_projection_pair_count",
        "identical_projection_pair_count",
        "comparison_signal_rate",
    ):
        assert k in rc["metrics"]
