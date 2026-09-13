"""Canonical, best-effort reproducibility identity for new Runtime work."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.config import PROJECT_ROOT, Settings
from app.prompts.career_planning import (
    BUSINESS_REPAIR_PROMPT_VERSION,
    FORMAT_REPAIR_PROMPT_VERSION,
    PLAN_PROMPT_VERSION,
)

MOCK_PLAN_PROMPT_VERSION = "mock_plan_stage6_context_v1"
MOCK_FORMAT_REPAIR_PROMPT_VERSION = "mock_format_repair_v1"
MOCK_BUSINESS_REPAIR_PROMPT_VERSION = "mock_business_repair_v1"
TOOL_CONTRACT_VERSION = "tool-registry-1.0"
CONTEXT_VERSION = "planning-context-stage6-v1"
MEMORY_VERSION = "three-layer-memory-stage6b-v1"
EVAL_HARNESS_VERSION = "eval-harness-v2"
_GIT_SHA = re.compile(r"^[0-9a-f]{7,64}$")


@dataclass(frozen=True, slots=True)
class RuntimeVersionIdentity:
    git_commit: str
    graph_version: str
    feature_stage: int
    prompt_versions: dict[str, str]
    tool_contract_version: str
    context_version: str
    memory_version: str
    search_version: str
    eval_harness_version: str

    @property
    def primary_prompt_version(self) -> str:
        return self.prompt_versions["career_planning"]


def resolve_git_commit(
    explicit: str | None,
    *,
    project_root: Path = PROJECT_ROOT,
) -> str:
    """Resolve an honest source revision without making startup depend on Git."""

    if explicit:
        normalized = explicit.strip().lower()
        if normalized == "unknown" or _GIT_SHA.fullmatch(normalized):
            return normalized
        return "unknown"
    try:
        result = subprocess.run(  # noqa: S603 -- fixed executable/arguments.
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            capture_output=True,
            check=False,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    candidate = result.stdout.strip().lower()
    return candidate if result.returncode == 0 and _GIT_SHA.fullmatch(candidate) else "unknown"


def build_runtime_identity(settings: Settings) -> RuntimeVersionIdentity:
    is_real_llm = settings.llm_provider == "openai_compatible"
    prompt_versions = {
        "career_planning": (
            PLAN_PROMPT_VERSION if is_real_llm else MOCK_PLAN_PROMPT_VERSION
        ),
        "format_repair": (
            FORMAT_REPAIR_PROMPT_VERSION
            if is_real_llm
            else MOCK_FORMAT_REPAIR_PROMPT_VERSION
        ),
        "business_repair": (
            BUSINESS_REPAIR_PROMPT_VERSION
            if is_real_llm
            else MOCK_BUSINESS_REPAIR_PROMPT_VERSION
        ),
    }
    return RuntimeVersionIdentity(
        git_commit=resolve_git_commit(settings.app_git_commit),
        graph_version=settings.agent_graph_version,
        feature_stage=settings.agent_feature_stage,
        prompt_versions=prompt_versions,
        tool_contract_version=TOOL_CONTRACT_VERSION,
        context_version=CONTEXT_VERSION,
        memory_version=MEMORY_VERSION,
        search_version=f"{settings.search_provider}-search-v1",
        eval_harness_version=EVAL_HARNESS_VERSION,
    )
