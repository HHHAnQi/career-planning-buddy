"""Build secret-free Provider configuration diagnostics."""

from app.core.config import Settings
from app.providers.llm_profiles import resolve_provider_profile
from app.schemas.configuration import ProviderConfigurationItem, ProviderConfigurationStatus


def build_provider_configuration_status(settings: Settings) -> ProviderConfigurationStatus:
    """Describe configured Provider modes without exposing credentials or endpoints."""
    planning_missing = _missing(
        ("LLM_API_KEY", settings.llm_api_key),
        ("LLM_BASE_URL", settings.llm_base_url),
        ("LLM_MODEL", settings.llm_model),
    ) if settings.llm_provider == "openai_compatible" else []
    judge_missing = _missing(
        ("JUDGE_LLM_API_KEY", settings.judge_llm_api_key),
        ("JUDGE_LLM_BASE_URL", settings.judge_llm_base_url),
        ("JUDGE_LLM_MODEL", settings.judge_llm_model),
    ) if settings.judge_llm_provider == "openai_compatible" else []
    search_missing = _missing(
        ("BAIDU_SEARCH_API_KEY", settings.baidu_search_api_key),
    ) if settings.search_provider == "baidu" else []
    embedding_missing = _missing(
        ("EMBEDDING_MODEL_PATH", settings.embedding_model_path),
    ) if settings.embedding_provider == "local" else []

    planning_provider: str = settings.llm_provider
    if settings.llm_provider == "openai_compatible" and settings.llm_base_url is not None:
        planning_provider = resolve_provider_profile(
            configured_name=settings.llm_provider_name,
            base_url=str(settings.llm_base_url),
        ).provider_id
    items = {
        "planning_llm": ProviderConfigurationItem(
            provider=planning_provider,
            configured=not planning_missing,
            real=settings.llm_provider != "mock",
            missing_fields=planning_missing,
        ),
        "judge_llm": ProviderConfigurationItem(
            provider=settings.judge_llm_provider,
            configured=not judge_missing,
            real=settings.judge_llm_provider == "openai_compatible",
            missing_fields=judge_missing,
        ),
        "search": ProviderConfigurationItem(
            provider=settings.search_provider,
            configured=not search_missing,
            real=settings.search_provider != "mock",
            missing_fields=search_missing,
        ),
        "embedding": ProviderConfigurationItem(
            provider=settings.embedding_provider,
            configured=not embedding_missing,
            real=settings.embedding_provider != "mock",
            missing_fields=embedding_missing,
        ),
    }
    warnings = [
        f"{name} uses {item.provider}"
        for name, item in items.items()
        if not item.real
    ]
    return ProviderConfigurationStatus(
        app_env=settings.app_env,
        eval_provider_mode=settings.eval_provider_mode,
        ready=all(item.configured for item in items.values()),
        providers=items,
        warnings=warnings,
    )


def _missing(*pairs: tuple[str, object]) -> list[str]:
    return [name for name, value in pairs if value is None]
