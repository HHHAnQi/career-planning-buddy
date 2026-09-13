"""Central Provider capability registry and task-specific model resolution."""

from __future__ import annotations

from urllib.parse import urlparse

from app.core.config import Settings
from app.providers.llm_contracts import LLMProviderProfile

_PROFILES: dict[str, LLMProviderProfile] = {
    "openai": LLMProviderProfile(provider_id="openai"),
    "openai_compatible": LLMProviderProfile(provider_id="openai_compatible"),
    "zhipu": LLMProviderProfile(
        provider_id="zhipu",
        supports_reasoning_control=True,
        reasoning_parameter="thinking",
    ),
    "deepseek": LLMProviderProfile(
        provider_id="deepseek",
        supports_reasoning_control=True,
        reasoning_parameter="thinking",
    ),
}


def resolve_provider_profile(*, configured_name: str, base_url: str) -> LLMProviderProfile:
    """Resolve compatibility once at composition time, not inside business Providers."""
    if configured_name != "auto":
        return _PROFILES[configured_name]
    host = (urlparse(base_url).hostname or "").lower()
    if host == "api.openai.com" or host.endswith(".openai.com"):
        return _PROFILES["openai"]
    if host == "open.bigmodel.cn" or host.endswith(".bigmodel.cn"):
        return _PROFILES["zhipu"]
    if host == "api.deepseek.com" or host.endswith(".deepseek.com"):
        return _PROFILES["deepseek"]
    return _PROFILES["openai_compatible"]


def model_for_operation(settings: Settings, operation: str) -> str:
    """Select a task model while preserving LLM_MODEL as the compatibility default."""
    assert settings.llm_model is not None
    overrides = {
        "goal_understanding": settings.llm_goal_understanding_model,
        "evidence_distillation": settings.llm_evidence_distillation_model,
    }
    return overrides.get(operation) or settings.llm_planning_model or settings.llm_model
