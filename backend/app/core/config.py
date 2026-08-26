"""Environment-backed application configuration."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    """Validated runtime configuration.

    The shared root ``.env`` contains values for Compose and the frontend, so
    unrelated keys are deliberately ignored here. API schemas remain strict.
    """

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Career Planning Buddy"
    app_env: Literal["local", "test", "development", "staging", "production"] = "local"
    backend_host: str = "127.0.0.1"
    backend_port: int = Field(default=8000, ge=1, le=65535)
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )
    database_url: str = "postgresql+asyncpg://localhost:5432/career_buddy"
    jwt_secret: SecretStr | None = Field(default=None, min_length=32)
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_expire_minutes: int = Field(default=1440, ge=1, le=10080)
    jwt_issuer: str = "career-planning-buddy"
    app_git_commit: str | None = Field(default=None, min_length=1, max_length=64)
    agent_graph_version: str = Field(default="stage6b-v1", min_length=1, max_length=64)
    agent_feature_stage: int = Field(default=6, ge=4, le=6)
    llm_provider: Literal["mock", "openai_compatible"] = "mock"
    llm_provider_name: Literal[
        "auto", "openai", "zhipu", "deepseek", "openai_compatible"
    ] = "auto"
    llm_api_key: SecretStr | None = Field(default=None, min_length=1)
    llm_base_url: AnyHttpUrl | None = None
    llm_model: str | None = Field(default=None, min_length=1, max_length=128)
    llm_planning_model: str | None = Field(default=None, min_length=1, max_length=128)
    llm_goal_understanding_model: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    llm_evidence_distillation_model: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    llm_planning_reasoning: Literal["off", "auto"] = "off"
    llm_goal_understanding_reasoning: Literal["off", "auto"] = "off"
    llm_evidence_distillation_reasoning: Literal["off", "auto"] = "off"
    llm_timeout_seconds: float = Field(default=30, gt=0, le=120)
    # Wire-level SSE streaming for openai_compatible providers. Off by
    # default: Mock/deterministic paths never stream, and streaming only
    # changes transport, never response semantics.
    llm_streaming_enabled: bool = False
    asr_provider: Literal["mock", "openai_compatible"] = "mock"
    asr_api_key: SecretStr | None = Field(default=None, min_length=1)
    asr_base_url: AnyHttpUrl | None = None
    asr_model: str = Field(default="whisper-1", min_length=1, max_length=128)
    asr_timeout_seconds: float = Field(default=30, gt=0, le=60)
    asr_max_audio_bytes: int = Field(default=5_000_000, ge=1024, le=20_000_000)
    asr_max_duration_seconds: float = Field(default=120, gt=0, le=300)
    search_provider: Literal["mock", "baidu"] = "mock"
    baidu_search_api_key: SecretStr | None = Field(default=None, min_length=1)
    baidu_search_base_url: AnyHttpUrl = AnyHttpUrl(
        "https://qianfan.baidubce.com/v2/ai_search/web_search"
    )
    baidu_search_edition: Literal["lite", "standard"] = "standard"
    baidu_search_max_results: int = Field(default=5, ge=1, le=10)
    baidu_search_timeout_seconds: float = Field(default=8, gt=0, le=30)
    rag_min_similarity: float = Field(default=0.35, ge=0, le=1)
    # Second-stage reranker for document retrieval. "mock" is a deterministic
    # lexical reranker (CI/Mock mode never touches the network); "tei" targets
    # a HuggingFace text-embeddings-inference /rerank endpoint (bge-reranker).
    rerank_provider: Literal["mock", "tei"] = "mock"
    rerank_base_url: AnyHttpUrl | None = None
    rerank_timeout_seconds: float = Field(default=8, gt=0, le=30)
    # Answerability gate: chunks scoring below this rerank score are
    # dropped; the search returns "insufficient evidence" instead of
    # forcing a weak match.
    rag_min_rerank_score: float = Field(default=0.001, ge=0, le=1)
    embedding_provider: Literal["mock", "local", "openai_compatible"] = "mock"
    embedding_model_path: Path | None = None
    embedding_api_key: SecretStr | None = Field(default=None, min_length=1)
    embedding_base_url: AnyHttpUrl | None = None
    embedding_api_model: str | None = Field(default=None, min_length=1, max_length=128)

    # PR-5: per-process eval provider mode. TrialRunner honours "fixture" /
    # "mock" / "live" when building the executor's providers (LLM + Search +
    # Embedding). Prod HTTP path ignores this field entirely.
    eval_provider_mode: Literal["mock", "fixture", "live"] = "mock"
    # PR-9b: live-mode bookkeeping knobs.
    eval_audit_live_calls: bool = True
    eval_provider_seed_mode: Literal["none", "provider_seed", "local_seed"] = (
        "provider_seed"
    )
    eval_live_max_attempts: int = Field(default=3, ge=1, le=5)
    eval_live_retry_base_seconds: float = Field(default=1, ge=0, le=30)
    eval_live_retry_max_seconds: float = Field(default=8, ge=0, le=60)
    # Trial-level concurrency is process-local and defaults to serial. It is
    # independent from the stricter live provider-call concurrency below.
    eval_trial_concurrency: int = Field(default=1, ge=1, le=8)
    eval_live_concurrency: int = Field(default=2, ge=1, le=8)
    eval_live_pacing_seconds: float = Field(default=0.5, ge=0, le=10)
    # PR-9c.2 Commit 3.4 (Stage A, Option E′): when set, TrialRunner
    # substitutes MockPlanningProvider with PairSmokePlanningProvider
    # bound to this profile, producing two fixture profiles
    # (compact_v1 / structured_v1) that are byte-different on
    # PLAN_PROJECTION. ``None`` = legacy MockPlanningProvider path.
    # Bound through a Settings-style switch (not execution_mode) so the
    # DB CHECK on eval_experiments.execution_mode does not need to grow.
    eval_pair_smoke_planning_profile: (
        Literal["compact_v1", "structured_v1"] | None
    ) = None
    embedding_model_name: str | None = Field(default=None, min_length=1, max_length=200)
    embedding_dim: int = Field(
        default=1024,
        ge=1,
        le=4096,
        validation_alias=AliasChoices("embedding_dim", "EMBEDDING_DIM", "EMBEDDING_DIMENSION"),
    )
    memory_semantic_retrieval_enabled: bool = True
    memory_retrieval_limit: int = Field(default=8, ge=1, le=20)
    memory_context_max_items: int = Field(default=5, ge=1, le=5)
    memory_context_max_chars: int = Field(default=1200, ge=100, le=10000)
    memory_min_similarity: float = Field(default=0.35, ge=0, le=1)
    memory_recency_half_life_days: int = Field(default=14, ge=1, le=365)
    tool_timeout_seconds: float = Field(default=8, gt=0, le=30)
    agent_max_llm_calls: int = Field(default=7, ge=1, le=7)
    agent_max_tool_rounds: int = Field(default=2, ge=0, le=2)
    agent_max_tool_calls: int = Field(default=4, ge=0, le=4)
    agent_max_total_tokens: int = Field(default=16000, ge=1)
    agent_max_input_tokens_per_call: int = Field(default=6000, ge=1)
    agent_max_output_tokens_per_call: int = Field(default=1500, ge=1)
    agent_deadline_seconds: int = Field(default=45, ge=1, le=300)
    agent_poll_interval_seconds: float = Field(default=0.05, gt=0, le=5)
    agent_heartbeat_seconds: float = Field(default=15, gt=0, le=60)
    agent_lease_seconds: float = Field(default=45, ge=5, le=300)
    agent_max_run_attempts: int = Field(default=3, ge=1, le=10)
    agent_worker_concurrency: int = Field(default=2, ge=1, le=16)

    # HTTP boundary guard: 0 disables request-rate limiting entirely
    # (test default); Compose deployments set a positive value via .env.
    rate_limit_per_minute: int = Field(default=0, ge=0, le=100_000)

    # Pairwise Judge credentials are intentionally independent from the
    # agent-under-test. Live Judge mode fails closed when any field is absent.
    judge_llm_provider: Literal["mock", "fixture", "openai_compatible"] = "fixture"
    judge_llm_api_key: SecretStr | None = Field(default=None, min_length=1)
    judge_llm_base_url: AnyHttpUrl | None = None
    judge_llm_model: str | None = Field(default=None, min_length=1, max_length=128)
    judge_llm_timeout_seconds: float = Field(default=30, gt=0, le=120)
    judge_llm_max_output_tokens: int = Field(default=800, ge=64, le=4096)
    judge_llm_temperature: float = Field(default=0.0, ge=0, le=2)

    @field_validator("database_url")
    @classmethod
    def require_async_postgresql(cls, value: str) -> str:
        """Reject malformed URLs and synchronous PostgreSQL drivers."""
        try:
            url = make_url(value)
        except ArgumentError as exc:
            raise ValueError("DATABASE_URL must be a valid SQLAlchemy URL") from exc

        if url.drivername != "postgresql+asyncpg":
            raise ValueError("DATABASE_URL must use the postgresql+asyncpg driver")
        if not url.database:
            raise ValueError("DATABASE_URL must include a database name")
        return value

    @field_validator("embedding_dim")
    @classmethod
    def require_migration_embedding_dimension(cls, value: int) -> int:
        """Keep Provider vectors compatible with the Stage 4 pgvector columns."""
        if value != 1024:
            raise ValueError("EMBEDDING_DIM must be 1024 for the current migration baseline")
        return value

    @field_validator(
        "llm_api_key",
        "llm_base_url",
        "llm_model",
        "llm_planning_model",
        "llm_goal_understanding_model",
        "llm_evidence_distillation_model",
        "asr_api_key",
        "asr_base_url",
        "judge_llm_api_key",
        "judge_llm_base_url",
        "judge_llm_model",
        "embedding_model_path",
        "embedding_model_name",
        "baidu_search_api_key",
        "app_git_commit",
        "eval_pair_smoke_planning_profile",
        mode="before",
    )
    @classmethod
    def empty_optional_values_are_unset(cls, value: object) -> object:
        """Allow empty template values while retaining strict real-provider checks."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def require_production_jwt_secret(self) -> "Settings":
        """Refuse to start a deployed environment without an explicit secret."""
        if self.app_env in {"staging", "production"} and self.jwt_secret is None:
            raise ValueError("JWT_SECRET is required in staging and production")
        if self.llm_provider == "openai_compatible":
            missing: list[str] = []
            if self.llm_api_key is None:
                missing.append("LLM_API_KEY")
            if self.llm_base_url is None:
                missing.append("LLM_BASE_URL")
            if self.llm_model is None:
                missing.append("LLM_MODEL")
            if missing:
                raise ValueError("openai_compatible requires configured " + ", ".join(missing))
        if self.judge_llm_provider == "openai_compatible":
            judge_missing: list[str] = []
            if self.judge_llm_api_key is None:
                judge_missing.append("JUDGE_LLM_API_KEY")
            if self.judge_llm_base_url is None:
                judge_missing.append("JUDGE_LLM_BASE_URL")
            if self.judge_llm_model is None:
                judge_missing.append("JUDGE_LLM_MODEL")
            if judge_missing:
                raise ValueError(
                    "openai_compatible judge requires configured "
                    + ", ".join(judge_missing)
                )
        if self.asr_provider == "openai_compatible":
            asr_missing: list[str] = []
            if self.asr_api_key is None:
                asr_missing.append("ASR_API_KEY")
            if self.asr_base_url is None:
                asr_missing.append("ASR_BASE_URL")
            if asr_missing:
                raise ValueError(
                    "openai_compatible ASR requires configured " + ", ".join(asr_missing)
                )
        if self.embedding_provider == "local":
            if self.embedding_model_path is None:
                raise ValueError("local embedding requires EMBEDDING_MODEL_PATH")
            if not self.embedding_model_path.is_dir():
                raise ValueError("EMBEDDING_MODEL_PATH must be an existing local directory")
        if self.search_provider == "baidu" and self.baidu_search_api_key is None:
            raise ValueError("baidu search requires BAIDU_SEARCH_API_KEY")
        if self.eval_provider_mode == "live" and self.llm_provider == "mock":
            raise ValueError("EVAL_PROVIDER_MODE=live requires a real LLM_PROVIDER")
        if self.app_env == "production":
            mock_providers = [
                name
                for name, is_mock in (
                    ("LLM_PROVIDER", self.llm_provider == "mock"),
                    ("SEARCH_PROVIDER", self.search_provider == "mock"),
                    ("EMBEDDING_PROVIDER", self.embedding_provider == "mock"),
                    ("ASR_PROVIDER", self.asr_provider == "mock"),
                )
                if is_mock
            ]
            if mock_providers:
                raise ValueError(
                    "production cannot use mock providers: " + ", ".join(mock_providers)
                )
        if self.eval_live_retry_max_seconds < self.eval_live_retry_base_seconds:
            raise ValueError(
                "EVAL_LIVE_RETRY_MAX_SECONDS must be >= "
                "EVAL_LIVE_RETRY_BASE_SECONDS"
            )
        if self.agent_lease_seconds <= self.agent_heartbeat_seconds:
            raise ValueError(
                "AGENT_LEASE_SECONDS must be greater than "
                "AGENT_HEARTBEAT_SECONDS"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""
    # Tests and CI must be reproducible from their explicit environment and
    # must never inherit developer-local provider credentials from .env.
    if os.getenv("APP_ENV", "").lower() == "test":
        return Settings(_env_file=None)
    return Settings()
