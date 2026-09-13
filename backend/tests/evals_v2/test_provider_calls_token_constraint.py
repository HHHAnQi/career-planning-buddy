"""PR-9c.2 Stage B pre-flight — provider_calls token CHECK matrix tests.

Migration ``20260815_0018`` split the overloaded single tokens CHECK into
two single-responsibility CHECKs so failed / cancelled LLM calls can
carry NULL tokens (no usage info on the failure path). These tests pin
the refined contract via direct ORM INSERT attempts against a real Run's
``provider_calls`` table -- six cells of the (kind, status, tokens)
matrix that must behave the same way in any environment that has run the
migration.

The bug these tests guard against: before 0018, Stage B's real-graph
retry path (which exercises ``MockPlanningProvider``'s
``PROVIDER_RATE_LIMITED`` injection) wrote an LLM-error row with NULL
tokens, tripped ``ck_provider_calls_tokens_pair``, and was swallowed by
``AgentRunExecutor.execute``'s ``except Exception:`` into
``AGENT_EXECUTION_FAILED`` -- masking the real cause.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.config import get_settings

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
from app.core.database import session_transaction
from app.models.provider_call import ProviderCall
from app.repositories.evals import EvalRepository
from app.services.evals import EvalService
from evals.v2.contracts import DatasetManifest, ExperimentCreate
from evals.v2.dataset_loader import filter_cases, load_dataset
from evals.v2.trial_runner import TrialRunner
from tests.test_agent_runtime import runtime_factory


def _config(manifest: DatasetManifest) -> ExperimentCreate:
    return ExperimentCreate(
        dataset_id=manifest.dataset_id,
        dataset_version=manifest.dataset_version,
        dataset_hash=manifest.source_sha256,
        git_commit="7d29a45",
        graph_version="stage5-v1",
        prompt_version="career-plan-v1",
        model_version="mock-v1",
        tool_version="tool-contract-v1",
        context_version="context-v1",
        memory_version="memory-v1",
        execution_mode="fixture_provider",
        variant_role="baseline",
        trial_count=1,
    )


async def _provision_trial(
    db_session: AsyncSession,
    db_connection: AsyncConnection,
) -> tuple[UUID, UUID]:
    """Run a real create-01 trial so we have a valid (run_id, trial_id)
    pair whose ``provider_calls`` FK targets actually exist."""

    bundle = filter_cases(load_dataset(), ["create-01"])
    _, trials = await EvalService(db_session).create_experiment(
        dataset=bundle, config=_config(bundle.manifest)
    )
    trial = trials[0]
    runner = TrialRunner(
        session_factory=runtime_factory(db_connection),
        settings=get_settings(),
    )
    await runner.run_trial(trial, bundle.cases[0])

    async with session_transaction(db_session):
        refreshed = await EvalRepository(db_session).get_trial(trial.id)
        assert refreshed is not None
        assert refreshed.run_id is not None
    return refreshed.run_id, trial.id


def _build_row(
    *, run_id: UUID, trial_id: UUID, sequence: int,
    provider_kind: str, provider_method: str,
    status: str, error_code: str | None,
    tokens_in: int | None, tokens_out: int | None,
) -> ProviderCall:
    """Construct a ``ProviderCall`` row with all NOT-NULL fields filled."""

    from evals.v2.contracts import canonical_sha256
    request = {"method": provider_method, "seed": sequence}
    return ProviderCall(
        id=uuid4(),
        run_id=run_id,
        trial_id=trial_id,
        sequence=sequence,
        provider_kind=provider_kind,
        provider_method=provider_method,
        logical_call_index=0,
        retry_attempt=0,
        request_projection=request,
        request_projection_hash=canonical_sha256(request),
        response_projection=None,
        response_projection_hash=None,
        status=status,
        error_code=error_code,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=0,
        model_id="mock-career-planner-v1" if provider_kind == "llm" else None,
        created_at=datetime.now(UTC),
    )


async def _attempt_insert(
    db_session: AsyncSession,
    *,
    run_id: UUID,
    trial_id: UUID,
    sequence: int,
    provider_kind: str,
    provider_method: str,
    status: str,
    error_code: str | None,
    tokens_in: int | None,
    tokens_out: int | None,
) -> None:
    """Insert one ``ProviderCall`` row via ORM under a savepoint and let
    the DB raise if any CHECK constraint is violated."""

    async with session_transaction(db_session):
        db_session.add(
            _build_row(
                run_id=run_id, trial_id=trial_id, sequence=sequence,
                provider_kind=provider_kind, provider_method=provider_method,
                status=status, error_code=error_code,
                tokens_in=tokens_in, tokens_out=tokens_out,
            )
        )
        await db_session.flush()


async def _expect_violation(
    thunk: Callable[[], Awaitable[None]],
) -> None:
    """Run ``thunk`` (a zero-arg callable returning a coroutine) and
    assert the awaited coroutine trips a CHECK violation."""

    with pytest.raises(Exception) as exc_info:  # noqa: PT011
        await thunk()
    msg = str(exc_info.value).lower()
    assert (
        "check" in msg or "constraint" in msg
    ), f"expected CHECK violation, got: {exc_info.value!r}"


# --- six-cell matrix ---------------------------------------------------------

@pytest.mark.asyncio
async def test_case1_successful_llm_call_with_tokens_is_allowed(
    db_connection: AsyncConnection, db_session: AsyncSession,
) -> None:
    """Case 1: ``llm`` + ``status='ok'`` + non-NULL tokens ⇒ PASS."""
    run_id, trial_id = await _provision_trial(db_session, db_connection)
    await _attempt_insert(
        db_session, run_id=run_id, trial_id=trial_id, sequence=10_000,
        provider_kind="llm", provider_method="generate_agent_turn",
        status="ok", error_code=None, tokens_in=100, tokens_out=50,
    )


@pytest.mark.asyncio
async def test_case2_failed_llm_call_with_null_tokens_is_allowed(
    db_connection: AsyncConnection, db_session: AsyncSession,
) -> None:
    """Case 2: ``llm`` + ``status='error'`` + NULL tokens ⇒ PASS.

    Regression guard for the Stage B BLOCKER: failed LLM provider calls
    (rate-limited / timeout / unavailable) carry no usage info, so NULL
    tokens must be acceptable.
    """
    run_id, trial_id = await _provision_trial(db_session, db_connection)
    await _attempt_insert(
        db_session, run_id=run_id, trial_id=trial_id, sequence=10_001,
        provider_kind="llm", provider_method="generate_agent_turn",
        status="error", error_code="PROVIDER_RATE_LIMITED",
        tokens_in=None, tokens_out=None,
    )


@pytest.mark.asyncio
async def test_case3_successful_llm_call_with_null_tokens_is_rejected(
    db_connection: AsyncConnection, db_session: AsyncSession,
) -> None:
    """Case 3: ``llm`` + ``status='ok'`` + NULL tokens ⇒ FAIL.

    Pins the unchanged success-path contract: a completed LLM call
    without token accounting is still a data-quality violation.
    """
    run_id, trial_id = await _provision_trial(db_session, db_connection)
    await _expect_violation(lambda: _attempt_insert(
        db_session, run_id=run_id, trial_id=trial_id, sequence=10_002,
        provider_kind="llm", provider_method="generate_agent_turn",
        status="ok", error_code=None, tokens_in=None, tokens_out=None,
    ))


@pytest.mark.asyncio
async def test_case4_embedding_call_with_null_tokens_is_allowed(
    db_connection: AsyncConnection, db_session: AsyncSession,
) -> None:
    """Case 4: ``embedding`` + ``status='ok'`` + NULL tokens ⇒ PASS."""
    run_id, trial_id = await _provision_trial(db_session, db_connection)
    await _attempt_insert(
        db_session, run_id=run_id, trial_id=trial_id, sequence=10_003,
        provider_kind="embedding", provider_method="embed",
        status="ok", error_code=None, tokens_in=None, tokens_out=None,
    )


@pytest.mark.asyncio
async def test_case5_embedding_call_with_non_null_tokens_is_rejected(
    db_connection: AsyncConnection, db_session: AsyncSession,
) -> None:
    """Case 5: ``embedding`` + non-NULL tokens ⇒ FAIL.

    Non-LLM kinds never carry usage info; tokens must stay NULL even on
    a successful call.
    """
    run_id, trial_id = await _provision_trial(db_session, db_connection)
    await _expect_violation(lambda: _attempt_insert(
        db_session, run_id=run_id, trial_id=trial_id, sequence=10_004,
        provider_kind="embedding", provider_method="embed",
        status="ok", error_code=None, tokens_in=100, tokens_out=50,
    ))


@pytest.mark.asyncio
async def test_case6_cancelled_llm_call_with_null_tokens_is_allowed(
    db_connection: AsyncConnection, db_session: AsyncSession,
) -> None:
    """Case 6 (bonus): ``llm`` + ``status='cancelled'`` + NULL ⇒ PASS.

    Pins the same exemption for the cancelled branch -- the recorder
    writes NULL tokens on cancel (no usage was returned before the
    CancelledError propagated), and pre-0018 the kind=llm clause alone
    forced non-NULL tokens, hiding the same latent bug as the error path.

    NB: ``error_code`` is NULL because ``ck_provider_calls_error_pair``
    ties ``status='cancelled'`` to NULL error_code (cancel is not an
    error); the exemption under test is the tokens contract, not the
    error-code contract.
    """
    run_id, trial_id = await _provision_trial(db_session, db_connection)
    await _attempt_insert(
        db_session, run_id=run_id, trial_id=trial_id, sequence=10_005,
        provider_kind="llm", provider_method="generate_agent_turn",
        status="cancelled", error_code=None,
        tokens_in=None, tokens_out=None,
    )
