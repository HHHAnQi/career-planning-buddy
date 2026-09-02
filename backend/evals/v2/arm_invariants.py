"""Arm-configuration invariants: a misconfigured experiment arm must be
machine-detected, not discovered months later in an interview.

Background: the first-round "bare model" baseline arm was not bare — the
direct variant still enjoyed deterministic memory pre-execution and
context injection, and the error survived a full 30-case run, grading,
and documentation. These invariants make that class of error impossible
to ship: violations mark the trial ``invalid_arm_configuration`` instead
of silently producing wrong conclusions.

Two checkpoints:
* pre-trial  — the Run's frozen config snapshot must match the variant's
  declared knobs (available_tools, memory_disabled, repair switches);
* post-trial — the observed trajectory must match (tool calls only from
  sanctioned sources, provider requests shaped as declared).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArmViolation:
    check: str
    detail: str


def _config(config_snapshot: dict[str, object]) -> dict[str, object]:
    return config_snapshot if isinstance(config_snapshot, dict) else {}


def check_pre_trial(
    agent_variant: str | None, config_snapshot: dict[str, object]
) -> list[ArmViolation]:
    """Validate the frozen run config against the declared variant arm."""
    violations: list[ArmViolation] = []
    cfg = _config(config_snapshot)
    tools = cfg.get("available_tools")
    memory_disabled = bool(cfg.get("memory_disabled"))
    if agent_variant == "direct_llm_v1":
        if not isinstance(tools, list) or tools:
            violations.append(
                ArmViolation(
                    "direct_llm_v1.tools_hidden",
                    f"available_tools must be empty, got {tools!r}",
                )
            )
        if not memory_disabled and tools:
            # direct + memory ON relies solely on pre-execution; the model
            # must never see an autonomous tool list.
            violations.append(
                ArmViolation(
                    "direct_llm_v1.no_autonomous_tools",
                    "memory-enabled direct arm must not expose tools",
                )
            )
    else:
        # Default agent arm: the memory tool must be available unless the
        # memory layer is explicitly ablated.
        if not memory_disabled and (
            not isinstance(tools, list) or "memory_lookup" not in tools
        ):
            violations.append(
                ArmViolation(
                    "agent_arm.memory_tool_present",
                    f"available_tools must include memory_lookup, got {tools!r}",
                )
            )
    return violations


def check_post_trial(
    agent_variant: str | None,
    config_snapshot: dict[str, object],
    tool_call_names: list[str],
) -> list[ArmViolation]:
    """Validate the observed trajectory against the declared variant arm."""
    violations: list[ArmViolation] = []
    cfg = _config(config_snapshot)
    memory_disabled = bool(cfg.get("memory_disabled"))
    if agent_variant == "direct_llm_v1" and memory_disabled:
        if tool_call_names:
            violations.append(
                ArmViolation(
                    "direct_llm_v1.bare_no_tool_calls",
                    f"true-bare arm executed tools: {sorted(set(tool_call_names))}",
                )
            )
    if agent_variant == "direct_llm_v1" and not memory_disabled:
        unexpected = set(tool_call_names) - {"memory_lookup"}
        if unexpected:
            violations.append(
                ArmViolation(
                    "direct_llm_v1.pre_execution_only",
                    f"unexpected tool calls beyond pre-executed memory_lookup: "
                    f"{sorted(unexpected)}",
                )
            )
    return violations
