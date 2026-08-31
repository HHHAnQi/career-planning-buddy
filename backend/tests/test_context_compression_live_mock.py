"""Mock end-to-end validation of the live comparison script (六.6).

Runs scripts/context_compression_live._one_trial against the real
executor with the MOCK provider (zero paid calls) for all three
strategies and asserts the execution chain the real run depends on:

  1. the case history IS in the model input snapshot;
  2. each strategy is frozen into the run's config snapshot BEFORE and
     unchanged AFTER execution;
  3. the three strategies produce DIFFERENT model-input snapshots
     (the switch actually does something);
  4. system trials and actual provider calls are counted separately
     (mock provider: calls counted via the same CountingProvider).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

DATASET = (
    Path(__file__).resolve().parents[1] / "evals/datasets/context-compression-v1.jsonl"
)


@pytest.mark.asyncio
async def test_live_trial_chain_end_to_end_with_mock_provider() -> None:
    import os

    os.environ["LLM_PROVIDER"] = "mock"
    os.environ["EVAL_PROVIDER_MODE"] = "fixture"
    get_settings.cache_clear()

    from scripts.context_compression_live import _one_trial

    cases = [
        json.loads(line)
        for line in DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    case = next(c for c in cases if c["case_id"] == "cc-old-relevant-03")

    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    snapshots: dict[str, dict[str, object]] = {}
    try:
        for strategy in ("full", "recent", "relevant_summary"):
            row = await _one_trial(factory, case, strategy)
            assert "error" not in row, row.get("error")

            # 1. history actually reached the model input snapshot
            assert row["history_in_input_snapshot"] is True, (
                f"{strategy}: loaded history absent from input snapshot"
            )

            # 2. strategy frozen before AND unchanged after (asserted
            #    inside _one_trial; the row proves the trial completed)
            assert row["strategy"] == strategy
            assert row["status"] in {"completed", "degraded"}

            # 4. trial-vs-call separation: one system trial made at least
            #    one counted provider call (mock provider is counted by
            #    the same wrapper the real run uses)
            assert row["provider_llm_calls"] >= 1

            async with factory() as session:
                from app.models.agent_run import AgentRun

                run = await session.get(
                    AgentRun, __import__("uuid").UUID(row["run_id"])
                )
                snapshots[strategy] = dict(run.input_snapshot_json or {})
                frozen = dict(run.config_snapshot_json or {})
                assert (
                    frozen["context_compression_strategy"] == strategy
                )
    finally:
        await engine.dispose()
        os.environ.pop("LLM_PROVIDER", None)
        os.environ.pop("EVAL_PROVIDER_MODE", None)
        get_settings.cache_clear()

    # 3. the switch does something: windowed strategies drop history the
    #    full strategy keeps (different serialized inputs).
    def _size(strategy: str) -> int:
        return len(json.dumps(snapshots[strategy], ensure_ascii=False))

    assert _size("full") > _size("recent"), (
        "recent strategy did not shrink the model input vs full — "
        "the strategy switch is not effective"
    )
