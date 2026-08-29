"""Dataset-level regression tests for the deterministic intent router."""

from evals.intent_router import evaluate_intent_router, load_intent_cases


def test_intent_router_dataset_is_balanced_across_supported_boundaries() -> None:
    cases = load_intent_cases()

    assert len(cases) == 23
    assert {case.expected_intent.value for case in cases} == {
        "create_plan",
        "replan",
        "navigate",
        "unsupported",
    }
    assert {case.expected_reason for case in cases} >= {
        "profile_incomplete",
        "intent_uncertain",
    }


def test_intent_router_dataset_passes_deterministically() -> None:
    first = evaluate_intent_router()
    second = evaluate_intent_router()

    assert first == second
    assert first["case_count"] == 23
    assert first["passed_cases"] == 23
    assert first["failed_cases"] == 0
    assert first["accuracy"] == 1.0
