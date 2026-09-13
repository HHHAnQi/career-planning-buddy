"""Content-addressed immutable Runtime Bundle creation."""

import json
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.agent_run import AgentRuntimeBundle
from app.runtime.versioning import build_runtime_identity
from app.schemas.agent_runs import RuntimeConfigSnapshot


def build_resume_runtime_bundle(
    settings: Settings, config: RuntimeConfigSnapshot
) -> dict[str, object]:
    identity = build_runtime_identity(settings)
    return {
        "schema_version": 1,
        "git_commit": identity.git_commit,
        "graph_version": config.graph_version,
        "prompt_versions": {
            **config.prompt_versions,
            "resume_optimization": "resume-optimization-evidence-v2",
        },
        "provider": config.provider,
        "model_id": config.model_alias,
        "model_parameters": {"temperature": 0, "structured_output": "json_object"},
        "tool_contract_versions": {
            "resume_gap_analyze": "2.0",
            "interview_evidence_retrieve": "2.0",
        },
        "context_algorithm_version": "resume-context-rrf-mmr-v2",
        "embedding_provider": settings.embedding_provider,
        "embedding_model_id": settings.embedding_model_name or settings.embedding_provider,
        "validator_version": "resume-faithfulness-v2",
        "eval_harness_version": identity.eval_harness_version,
        "budget": {
            "max_llm_calls": config.max_llm_calls,
            "max_tool_calls": config.max_tool_calls,
            "max_total_tokens": config.max_total_tokens,
            "max_input_tokens_per_call": config.max_input_tokens_per_call,
            "max_output_tokens_per_call": config.max_output_tokens_per_call,
            "deadline_seconds": config.deadline_seconds,
        },
    }


async def get_or_create_runtime_bundle(
    session: AsyncSession, payload: dict[str, object]
) -> AgentRuntimeBundle:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    bundle_hash = sha256(encoded).hexdigest()
    existing = await session.scalar(
        select(AgentRuntimeBundle).where(AgentRuntimeBundle.bundle_hash == bundle_hash)
    )
    if existing is not None:
        return existing
    row = AgentRuntimeBundle(bundle_hash=bundle_hash, bundle_json=payload)
    session.add(row)
    await session.flush()
    return row
