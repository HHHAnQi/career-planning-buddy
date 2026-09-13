"""Stage 6A fixed-dataset regression gate."""

import pytest

from evals.stage6_runner import load_stage6_cases, run_stage6_evaluation


def test_stage6_dataset_is_frozen_at_twelve_cases() -> None:
    cases = load_stage6_cases()
    assert len(cases) == 12
    assert len({case.case_id for case in cases}) == 12


@pytest.mark.asyncio
async def test_stage6_dataset_passes_all_deterministic_graders() -> None:
    report = await run_stage6_evaluation(persist=False)

    assert report["case_count"] == 12
    assert report["passed_cases"] == 12
    assert report["failed_cases"] == 0
    assert report["pass_rate"] == 1.0
