"""Arm-configuration invariant tests (fix 1).

The first-round "bare model" baseline arm was misconfigured and the error
survived a full experiment. These tests pin the machine guard: a
misconfigured arm must be detected pre-trial and post-trial.
"""

from __future__ import annotations

from evals.v2.arm_invariants import check_post_trial, check_pre_trial

DIRECT = "direct_llm_v1"


def test_true_bare_arm_config_passes_pre_trial() -> None:
    violations = check_pre_trial(
        DIRECT,
        {"available_tools": [], "memory_disabled": True},
    )
    assert violations == []


def test_fake_bare_arm_is_rejected_pre_trial() -> None:
    # The exact first-round mistake: direct variant claiming to be bare
    # while still exposing the autonomous tool list.
    violations = check_pre_trial(
        DIRECT,
        {"available_tools": ["memory_lookup", "rag_retrieve", "web_search"]},
    )
    assert any(v.check == "direct_llm_v1.tools_hidden" for v in violations)


def test_default_arm_without_memory_tool_is_rejected() -> None:
    violations = check_pre_trial(
        None,
        {"available_tools": ["rag_retrieve", "web_search"], "memory_disabled": False},
    )
    assert any(v.check == "agent_arm.memory_tool_present" for v in violations)


def test_ablated_default_arm_is_legitimate() -> None:
    violations = check_pre_trial(
        None,
        {"available_tools": ["rag_retrieve", "web_search"], "memory_disabled": True},
    )
    assert violations == []


def test_true_bare_arm_with_tool_calls_fails_post_trial() -> None:
    violations = check_post_trial(
        DIRECT,
        {"available_tools": [], "memory_disabled": True},
        tool_call_names=["memory_lookup"],
    )
    assert any(v.check == "direct_llm_v1.bare_no_tool_calls" for v in violations)


def test_direct_arm_extra_tools_fail_post_trial() -> None:
    violations = check_post_trial(
        DIRECT,
        {"available_tools": [], "memory_disabled": False},
        tool_call_names=["memory_lookup", "web_search"],
    )
    assert any(v.check == "direct_llm_v1.pre_execution_only" for v in violations)
