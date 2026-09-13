"""Settings validation and environment precedence tests."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from pytest import MonkeyPatch

from app.core.config import PROJECT_ROOT, Settings, get_settings
from app.core.provider_status import build_provider_configuration_status
from app.harness.snapshots import SnapshotService
from scripts.audit_config import audit_configuration
from scripts.migrate_legacy_env import migrate_lines


def test_settings_load_safe_defaults(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("BACKEND_PORT", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.app_env == "local"
    assert settings.backend_port == 8000
    assert settings.database_url == "postgresql+asyncpg://localhost:5432/career_buddy"
    assert settings.llm_provider == "mock"


def test_environment_overrides_settings(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BACKEND_PORT", "9001")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://test_user:test_password@localhost:5432/test_db",
    )

    settings = Settings(_env_file=None)

    assert settings.app_env == "test"
    assert settings.backend_port == 9001
    assert settings.database_url.endswith("/test_db")


def test_cached_test_settings_ignore_env_files(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test_only")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.app_env == "test"
        assert settings.database_url == "postgresql+asyncpg://localhost/test_only"
    finally:
        get_settings.cache_clear()


def test_synchronous_database_url_is_rejected() -> None:
    with pytest.raises(ValidationError, match=r"postgresql\+asyncpg"):
        Settings(_env_file=None, database_url="postgresql://localhost:5432/career_buddy")


def test_production_requires_explicit_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET is required"):
        Settings(_env_file=None, app_env="production", jwt_secret=None)


def test_openai_compatible_requires_complete_configuration() -> None:
    with pytest.raises(
        ValidationError,
        match="LLM_API_KEY, LLM_BASE_URL, LLM_MODEL",
    ):
        Settings(
            _env_file=None,
            llm_provider="openai_compatible",
            llm_api_key="",
            llm_base_url="",
            llm_model="",
        )


def test_openai_compatible_configuration_is_validated_without_exposing_key() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="openai_compatible",
        llm_api_key="unit-test-secret",
        llm_base_url="https://llm.example.test/v1",
        llm_model="career-model",
    )

    assert settings.llm_provider == "openai_compatible"
    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == "unit-test-secret"
    assert "unit-test-secret" not in repr(settings)


def test_llm_provider_profile_and_task_models_are_explicit() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="openai_compatible",
        llm_provider_name="zhipu",
        llm_api_key="unit-test-secret",
        llm_base_url="https://open.bigmodel.cn/api/paas/v4",
        llm_model="glm-default",
        llm_planning_model="glm-planning",
        llm_goal_understanding_model="glm-goal",
    )

    status = build_provider_configuration_status(settings)
    snapshot = SnapshotService.build_config(settings)

    assert status.providers["planning_llm"].provider == "zhipu"
    assert snapshot.model_alias == "glm-planning"


def test_openai_compatible_judge_requires_dedicated_configuration() -> None:
    with pytest.raises(ValidationError, match="JUDGE_LLM_API_KEY"):
        Settings(
            _env_file=None,
            judge_llm_provider="openai_compatible",
            judge_llm_api_key="",
            judge_llm_base_url="",
            judge_llm_model="",
        )


def test_live_eval_requires_real_planning_provider() -> None:
    with pytest.raises(ValidationError, match="live requires a real LLM_PROVIDER"):
        Settings(_env_file=None, eval_provider_mode="live", llm_provider="mock")


def test_blank_optional_eval_profile_is_treated_as_unset() -> None:
    settings = Settings(_env_file=None, eval_pair_smoke_planning_profile="")

    assert settings.eval_pair_smoke_planning_profile is None


def test_production_rejects_mock_providers() -> None:
    with pytest.raises(ValidationError, match="production cannot use mock providers"):
        Settings(
            _env_file=None,
            app_env="production",
            jwt_secret="production-secret-that-is-long-enough",
        )


def test_provider_status_is_secret_free_and_reports_mock_warnings() -> None:
    status = build_provider_configuration_status(Settings(_env_file=None))

    assert status.ready is True
    assert status.providers["planning_llm"].provider == "mock"
    assert status.providers["planning_llm"].real is False
    assert status.warnings
    assert "API_KEY" not in status.model_dump_json()


def test_configuration_template_matches_settings_and_compose_contract() -> None:
    assert audit_configuration() == []


def test_configuration_audit_detects_a_compose_mapping_omission(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text(
        (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    compose = (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")
    compose = "\n".join(
        line for line in compose.splitlines() if "TOOL_TIMEOUT_SECONDS:" not in line
    )
    (tmp_path / "compose.yaml").write_text(compose, encoding="utf-8")
    (tmp_path / "compose.embedding.yaml").write_text(
        (PROJECT_ROOT / "compose.embedding.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    assert "compose backend environment is missing Settings field TOOL_TIMEOUT_SECONDS" in (
        audit_configuration(tmp_path)
    )


def test_legacy_environment_migration_preserves_canonical_secret() -> None:
    migrated, changed = migrate_lines(
        [
            "LLM_API_KEY=canonical-secret\n",
            "COMPOSE_LLM_API_KEY=legacy-secret\n",
            "COMPOSE_SEARCH_PROVIDER=baidu\n",
        ]
    )

    assert "COMPOSE_LLM_API_KEY" in changed
    assert "COMPOSE_SEARCH_PROVIDER" in changed
    assert "LLM_API_KEY=canonical-secret\n" in migrated
    assert "SEARCH_PROVIDER=baidu\n" in migrated
    assert all("legacy-secret" not in line for line in migrated)


def test_embedding_dimension_must_match_pgvector_migration() -> None:
    with pytest.raises(ValidationError, match="EMBEDDING_DIM must be 1024"):
        Settings(_env_file=None, embedding_dim=768)


def test_memory_context_settings_are_bounded_and_snapshot_safe() -> None:
    settings = Settings(
        _env_file=None,
        memory_semantic_retrieval_enabled=False,
        memory_retrieval_limit=8,
        memory_context_max_items=5,
        memory_context_max_chars=1200,
        memory_min_similarity=0.35,
        memory_recency_half_life_days=14,
    )

    assert settings.memory_semantic_retrieval_enabled is False
    assert settings.memory_context_max_items == 5
    with pytest.raises(ValidationError):
        Settings(_env_file=None, memory_context_max_items=6)
