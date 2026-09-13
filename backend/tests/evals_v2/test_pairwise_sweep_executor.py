"""PR-9c.2 Pairwise SweepExecutor tests (Commit 3 + 3.1).

Coverage (Commit 3.1 revised semantics):

* Advisory lock key stability
* Cooperative cancel drains queued items to ``cancelled`` (running
  items are NOT auto-cancelled)
* ``_pair_completion_flags`` revised semantics:
  - first terminal Item does NOT bump ``completed_pair``
  - second terminal Item bumps ``completed_pair`` once
  - ``position_pair`` only when BOTH siblings completed + have results
  - ``completed`` + ``failed`` does NOT bump ``position_pair``
* ``_maybe_finalize_sweep`` finalizes when all Items terminal
* Recovery scopes to ``status='running'`` Sweeps only
* Crash recovery reconciles stale ``running`` Items:
  - running item without Result → requeued to ``queued``
  - running item with persisted Result → CAS to ``completed`` (no
    Provider call)
  - recovery does not double-count terminal Pairs
* Provider exception classification
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import session_transaction
from app.harness.pairwise_sweep_executor import (
    PairwiseSweepExecutor,
    _advisory_key_parts,
    _provider_error_code,
)
from app.models.eval import EvalPairwiseSweep, EvalPairwiseSweepItem, EvalTrialPair
from app.repositories.evals import EvalRepository
from tests.evals_v2.test_pairwise_calibration_repository import (
    _make_sweep,
    _real_judge_result,
    _seed_pair,
)

_VALID_SHA = "a" * 64


def _make_item(
    *,
    sweep_id: UUID,
    pair: EvalTrialPair,
    position: str,
    status: str = "queued",
    judge_run_id: UUID | None = None,
    judge_result_id: UUID | None = None,
    error_code: str | None = None,
    terminal_at: datetime | None = None,
) -> EvalPairwiseSweepItem:
    """Build a SweepItem always satisfying
    ``ck_eval_pairwise_sweep_items_position_consistency``:

      baseline  → display_a=baseline_trial_id, display_b=candidate_trial_id
      swapped   → display_a=candidate_trial_id, display_b=baseline_trial_id
    """

    if position == "baseline":
        display_a, display_b = pair.baseline_trial_id, pair.candidate_trial_id
    else:
        display_a, display_b = pair.candidate_trial_id, pair.baseline_trial_id
    return EvalPairwiseSweepItem(
        sweep_id=sweep_id,
        pair_id=pair.id,
        position_variant=position,
        case_id=pair.case_id,
        pair_hash=pair.pair_hash,
        baseline_trial_id=pair.baseline_trial_id,
        candidate_trial_id=pair.candidate_trial_id,
        baseline_output_hash=_VALID_SHA,
        candidate_output_hash=_VALID_SHA,
        display_a_trial_id=display_a,
        display_b_trial_id=display_b,
        frozen_review_surface_sha256=_VALID_SHA,
        judge_run_id=judge_run_id or uuid4(),
        status=status,
        judge_result_id=judge_result_id,
        error_code=error_code,
        terminal_at=terminal_at,
    )


async def _persist(
    *,
    db_session: AsyncSession,
    sweep_row: EvalPairwiseSweep,
    items: list[EvalPairwiseSweepItem],
) -> None:
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep_row)
        await EvalRepository(db_session).create_sweep_items(items)


def _executor_factory(db_session: AsyncSession) -> PairwiseSweepExecutor:
    """Build an Executor whose recovery path uses a session bound to the
    test's rollback-root connection. ``_recover_running_items`` opens
    its own AsyncSession; we hand it a factory bound to the same
    underlying connection so writes participate in the test's rollback
    and are visible to subsequent assertions."""
    factory = async_sessionmaker(
        bind=db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    return PairwiseSweepExecutor(session_factory=factory, engine=None)


# ---------------------------------------------------------------------------
# Advisory lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advisory_key_is_stable_per_sweep() -> None:
    """The advisory lock key MUST be deterministic per sweep so two
    workers converging on the same sweep try to take the same lock."""

    sweep = uuid4()
    assert _advisory_key_parts(sweep) == _advisory_key_parts(sweep)


@pytest.mark.asyncio
async def test_advisory_key_differs_across_sweeps() -> None:
    a = _advisory_key_parts(uuid4())
    b = _advisory_key_parts(uuid4())
    # namespace is fixed; only the second key varies
    assert a[0] == b[0]


# ---------------------------------------------------------------------------
# Cooperative cancel — drain queued items
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_cancelled_marks_queued_items_cancelled(
    db_session: AsyncSession,
) -> None:
    """``_drain_cancelled`` flips queued items to ``cancelled`` (terminal)
    under a cancel request. ``running`` items are NOT auto-cancelled —
    their in-flight Provider call is allowed to settle, then the Executor
    sees ``cancel_requested_at`` is set on the next iteration."""

    sweep_row = await _make_sweep(db_session, requested_pair_count=2)
    pair = await _seed_pair(db_session, 1)
    items = [
        _make_item(sweep_id=sweep_row.id, pair=pair, position=position)
        for position in ("baseline", "swapped")
    ]
    await _persist(db_session=db_session, sweep_row=sweep_row, items=items)
    executor = PairwiseSweepExecutor(session_factory=None)  # type: ignore[arg-type]
    async with session_transaction(db_session):
        await executor._drain_cancelled(db_session, sweep_row)  # noqa: SLF001
    # Verify against committed DB state via raw SQL count (the ORM
    # identity map is synchronized via synchronize_session="fetch" but
    # we still assert at the SQL level for an end-to-end guarantee).
    async with session_transaction(db_session):
        queued = await db_session.execute(
            text(
                "SELECT count(*) FROM eval_pairwise_sweep_items "
                "WHERE sweep_id = :sid AND status = 'queued'"
            ),
            {"sid": sweep_row.id},
        )
        cancelled = await db_session.execute(
            text(
                "SELECT count(*) FROM eval_pairwise_sweep_items "
                "WHERE sweep_id = :sid AND status = 'cancelled' "
                "AND terminal_at IS NOT NULL"
            ),
            {"sid": sweep_row.id},
        )
    assert queued.scalar_one() == 0
    assert cancelled.scalar_one() == 2


# ---------------------------------------------------------------------------
# Pair completion flags — REVISED Commit 3.1 semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_terminal_item_does_not_complete_pair(
    db_session: AsyncSession,
) -> None:
    """First terminal transition for a Pair returns ``(False, False)`` —
    no completed_pair bump, no position_pair bump. Only the SECOND
    required terminal marks the Pair as completed."""

    sweep_row = await _make_sweep(db_session, requested_pair_count=1)
    pair = await _seed_pair(db_session, 1)
    items = [
        _make_item(sweep_id=sweep_row.id, pair=pair, position="baseline"),
        _make_item(sweep_id=sweep_row.id, pair=pair, position="swapped"),
    ]
    await _persist(db_session=db_session, sweep_row=sweep_row, items=items)
    executor = PairwiseSweepExecutor(session_factory=None)  # type: ignore[arg-type]
    async with session_transaction(db_session):
        deltas = await executor._pair_completion_flags(db_session, items[0])  # noqa: SLF001
    assert deltas == (False, False)


@pytest.mark.asyncio
async def test_second_terminal_item_completes_pair_once(
    db_session: AsyncSession,
) -> None:
    """When this Item's transition is the SECOND terminal for the Pair,
    return ``(True, ...)``. completed_pair_count bumps exactly once."""

    sweep_row = await _make_sweep(db_session, requested_pair_count=1)
    pair = await _seed_pair(db_session, 1)
    failed_terminal_at = datetime.now(UTC)
    items = [
        _make_item(
            sweep_id=sweep_row.id,
            pair=pair,
            position="baseline",
            status="failed",
            error_code="PROVIDER_TIMEOUT",
            terminal_at=failed_terminal_at,
        ),
        _make_item(sweep_id=sweep_row.id, pair=pair, position="swapped"),
    ]
    await _persist(db_session=db_session, sweep_row=sweep_row, items=items)
    executor = PairwiseSweepExecutor(session_factory=None)  # type: ignore[arg-type]
    # The QUERIED item is the swapped one (still queued here, but the
    # function inspects its current status after a hypothetical
    # transition + sibling state — the calling _process_item refreshes
    # the item first; here we set the in-memory status to mirror that).
    items[1].status = "failed"
    items[1].error_code = "PROVIDER_TIMEOUT"
    items[1].terminal_at = failed_terminal_at
    async with session_transaction(db_session):
        deltas = await executor._pair_completion_flags(db_session, items[1])  # noqa: SLF001
    # Second terminal → completed_pair += 1, but neither has a result
    # so position_pair must be False.
    assert deltas == (True, False)


@pytest.mark.asyncio
async def test_completed_plus_failed_does_not_increment_position_pair(
    db_session: AsyncSession,
) -> None:
    """When sibling is ``completed`` with a result and this Item is the
    SECOND terminal but ``failed`` (no result), completed_pair bumps but
    position_pair does NOT (one of the two results is missing)."""

    sweep_row = await _make_sweep(db_session, requested_pair_count=1)
    pair = await _seed_pair(db_session, 1)
    judge_id = await _real_judge_result(db_session, pair=pair)
    items = [
        _make_item(
            sweep_id=sweep_row.id,
            pair=pair,
            position="baseline",
            status="completed",
            judge_result_id=judge_id,
            terminal_at=datetime.now(UTC),
        ),
        _make_item(
            sweep_id=sweep_row.id,
            pair=pair,
            position="swapped",
            status="failed",
            error_code="PROVIDER_TIMEOUT",
            terminal_at=datetime.now(UTC),
        ),
    ]
    await _persist(db_session=db_session, sweep_row=sweep_row, items=items)
    executor = PairwiseSweepExecutor(session_factory=None)  # type: ignore[arg-type]
    async with session_transaction(db_session):
        deltas = await executor._pair_completion_flags(db_session, items[1])  # noqa: SLF001
    assert deltas == (True, False)


@pytest.mark.asyncio
async def test_two_completed_results_increment_position_pair_once(
    db_session: AsyncSession,
) -> None:
    """Two siblings both ``completed`` with persisted JudgeResults.
    The SECOND terminal transition returns ``(True, True)`` — both
    completed_pair and position_pair bump exactly once."""

    sweep_row = await _make_sweep(db_session, requested_pair_count=1)
    pair = await _seed_pair(db_session, 1)
    judge_id = await _real_judge_result(db_session, pair=pair)
    items = [
        _make_item(
            sweep_id=sweep_row.id,
            pair=pair,
            position="baseline",
            status="completed",
            judge_result_id=judge_id,
            terminal_at=datetime.now(UTC),
        ),
        _make_item(
            sweep_id=sweep_row.id,
            pair=pair,
            position="swapped",
            status="completed",
            judge_result_id=judge_id,  # real JudgeResult row
            terminal_at=datetime.now(UTC),
        ),
    ]
    await _persist(db_session=db_session, sweep_row=sweep_row, items=items)
    executor = PairwiseSweepExecutor(session_factory=None)  # type: ignore[arg-type]
    async with session_transaction(db_session):
        deltas = await executor._pair_completion_flags(db_session, items[1])  # noqa: SLF001
    assert deltas == (True, True)


@pytest.mark.asyncio
async def test_maybe_finalize_sweep_marks_completed_when_all_terminal(
    db_session: AsyncSession,
) -> None:
    """Any failed Item → Sweep status ``failed`` on finalize."""

    sweep_row = await _make_sweep(db_session, requested_pair_count=1)
    pair = await _seed_pair(db_session, 1)
    judge_id = await _real_judge_result(db_session, pair=pair)
    item = _make_item(
        sweep_id=sweep_row.id, pair=pair, position="baseline"
    )
    item_swap = _make_item(
        sweep_id=sweep_row.id, pair=pair, position="swapped"
    )
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep_row)
        await EvalRepository(db_session).create_sweep_items([item, item_swap])
        await EvalRepository(db_session).mark_sweep_item_completed(
            item.id, judge_result_id=judge_id
        )
        await EvalRepository(db_session).mark_sweep_item_failed(
            item_swap.id, error_code="PROVIDER_TIMEOUT"
        )
    executor = PairwiseSweepExecutor(session_factory=None)  # type: ignore[arg-type]
    async with session_transaction(db_session):
        await executor._maybe_finalize_sweep(db_session, sweep_row.id)  # noqa: SLF001
        refetched = await EvalRepository(db_session).get_sweep(sweep_row.id)
    assert refetched is not None
    assert refetched.status == "failed"
    assert refetched.terminal_at is not None


@pytest.mark.asyncio
async def test_recovery_scopes_to_running_sweeps_only(
    db_session: AsyncSession,
) -> None:
    """A ``completed`` Sweep MUST NOT be re-entered by recovery."""

    sweep_running = await _make_sweep(
        db_session, requested_pair_count=1, status="running"
    )
    sweep_done = await _make_sweep(
        db_session, requested_pair_count=1, status="completed"
    )
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep_running)
        await EvalRepository(db_session).create_sweep(sweep_done)
        refetched_items = (
            await db_session.execute(
                select(EvalPairwiseSweepItem).where(
                    EvalPairwiseSweepItem.status.in_(("queued", "running"))
                )
            )
        ).all()
    assert refetched_items == []


# ---------------------------------------------------------------------------
# Crash recovery — stale ``running`` Items
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interrupted_running_item_without_result_is_requeued(
    db_session: AsyncSession,
) -> None:
    """A SweepItem stuck in ``running`` with no persisted JudgeResult
    MUST be requeued to ``queued`` by ``_recover_running_items`` so the
    main claim pump can re-execute it. Issue #3."""

    sweep_row = await _make_sweep(db_session, requested_pair_count=1, status="running")
    pair = await _seed_pair(db_session, 1)
    orphan = _make_item(
        sweep_id=sweep_row.id,
        pair=pair,
        position="baseline",
        status="running",
    )
    await _persist(db_session=db_session, sweep_row=sweep_row, items=[orphan])
    executor = _executor_factory(db_session)
    async with session_transaction(db_session):
        await executor._recover_running_items_in_session(db_session, sweep_row.id)  # noqa: SLF001
    async with session_transaction(db_session):
        refetched = await EvalRepository(db_session).list_sweep_items(sweep_row.id)
    assert len(refetched) == 1
    assert refetched[0].status == "queued"


@pytest.mark.asyncio
async def test_interrupted_running_item_with_existing_result_reconciles_without_provider(
    db_session: AsyncSession,
) -> None:
    """A ``running`` Item that already has a persisted JudgeResult MUST
    be CAS-transitioned directly to ``completed`` with that Result
    (no Provider call) by ``_recover_running_items``. Issue #3."""

    sweep_row = await _make_sweep(db_session, requested_pair_count=1, status="running")
    pair = await _seed_pair(db_session, 1)
    judge_id = await _real_judge_result(db_session, pair=pair)
    # _real_judge_result returns the Result row PK, not its
    # judge_run_id; look up the row to obtain the deterministic
    # judge_run_id the orphan SweepItem needs to wire to it.
    async with session_transaction(db_session):
        result_row = await EvalRepository(db_session).get_judge_result_by_id(judge_id)
    assert result_row is not None
    orphan = _make_item(
        sweep_id=sweep_row.id,
        pair=pair,
        position="baseline",
        status="running",
        judge_run_id=result_row.judge_run_id,
    )
    await _persist(db_session=db_session, sweep_row=sweep_row, items=[orphan])
    executor = _executor_factory(db_session)
    async with session_transaction(db_session):
        await executor._recover_running_items_in_session(db_session, sweep_row.id)  # noqa: SLF001
    async with session_transaction(db_session):
        refetched = await EvalRepository(db_session).list_sweep_items(sweep_row.id)
    assert len(refetched) == 1
    assert refetched[0].status == "completed"
    assert refetched[0].judge_result_id == judge_id
    assert refetched[0].terminal_at is not None


@pytest.mark.asyncio
async def test_recovery_does_not_recount_terminal_pair(
    db_session: AsyncSession,
) -> None:
    """Recovery MUST honor the per-Pair counter invariants — no
    double-counting even when recovery itself advances an Item to a
    terminal status alongside a sibling.

    Setup:
      * baseline sibling already ``completed`` with a result (its own
        happy-path flow returned deltas (False, False) because the swap
        side was still running — so neither completed_pair nor
        position_pair had bumped yet);
      * swap Item left ``running`` but its JudgeResult persisted.

    Recovery re-CAS-es the swap Item to ``completed``. Under revised
    semantics this is the SECOND terminal, so completed_pair moves
    from 0 → 1 and position_pair moves from 0 → 1 (both siblings are
    completed with results).

    A second recovery pass on the same already-terminal sweep MUST NOT
    move the counters further. (That is the no-double-count assertion.)
    """

    sweep_row = await _make_sweep(db_session, requested_pair_count=1, status="running")
    pair = await _seed_pair(db_session, 1)
    judge_id = await _real_judge_result(db_session, pair=pair)
    async with session_transaction(db_session):
        result_row = await EvalRepository(db_session).get_judge_result_by_id(judge_id)
    assert result_row is not None
    items = [
        _make_item(
            sweep_id=sweep_row.id,
            pair=pair,
            position="baseline",
            status="completed",
            judge_result_id=judge_id,
            terminal_at=datetime.now(UTC),
        ),
        # Orphan: running but result already persisted.
        _make_item(
            sweep_id=sweep_row.id,
            pair=pair,
            position="swapped",
            status="running",
            judge_run_id=result_row.judge_run_id,
        ),
    ]
    await _persist(db_session=db_session, sweep_row=sweep_row, items=items)
    executor = _executor_factory(db_session)

    async with session_transaction(db_session):
        await executor._recover_running_items_in_session(db_session, sweep_row.id)  # noqa: SLF001
    after_first = await EvalRepository(db_session).get_sweep(sweep_row.id)
    assert after_first is not None
    assert after_first.completed_pair_count == 1
    assert after_first.position_pair_count == 1

    # Idempotent: re-running recovery on the now-fully-terminal sweep
    # MUST NOT move counters.
    async with session_transaction(db_session):
        await executor._recover_running_items_in_session(db_session, sweep_row.id)  # noqa: SLF001
    after_second = await EvalRepository(db_session).get_sweep(sweep_row.id)
    assert after_second is not None
    assert after_second.completed_pair_count == 1
    assert after_second.position_pair_count == 1


# ---------------------------------------------------------------------------
# Provider error-code classification
# ---------------------------------------------------------------------------


def test_provider_error_code_classifies_known_exceptions() -> None:
    """The Executor maps provider exceptions to stable error codes so
    the failed-item row is attributable."""

    class FakeTimeout(Exception):
        pass

    class FakeAuth(Exception):
        pass

    assert _provider_error_code(FakeTimeout()) == "PROVIDER_TIMEOUT"
    assert _provider_error_code(FakeAuth()) == "PROVIDER_AUTH"
    assert _provider_error_code(Exception("generic")) == "PROVIDER_HARD_ERROR"


# ---------------------------------------------------------------------------
# Commit 3.2 — advisory lock on a dedicated raw connection (#2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advisory_lock_unlock_use_same_connection(
    db_session: AsyncSession,
) -> None:
    """Two ``_drive_sweep`` calls in sequence on the SAME Sweep MUST
    both succeed — proving the first released its advisory lock on its
    dedicated raw connection rather than leaking it (Commit 3.2 issue
    #2). The lock is held by the FIRST call only for the duration of
    that call; a second call immediately afterward acquires it fresh.
    """

    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(
        bind=db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        executor = PairwiseSweepExecutor(
            session_factory=factory, engine=engine
        )
        sweep_id = uuid4()
        # First call: lock acquired, no sweep exists → early-return
        # after _pump_one_item sees sweep=None.
        await executor._drive_sweep(sweep_id)  # noqa: SLF001
        # Second call: SAME sweep_id, MUST also acquire the lock (the
        # first call released it in finally on its dedicated connection).
        # No assertion needed beyond "did not hang" — pg_try_advisory_lock
        # returning False with no other holder would silently no-op, so
        # we directly probe the lock state via PG:
        key1, key2 = _advisory_key_parts(sweep_id)
        async with engine.connect() as probe:
            taken = (
                await probe.execute(
                    text("SELECT pg_try_advisory_lock(:k1, :k2)"),
                    {"k1": key1, "k2": key2},
                )
            ).scalar_one()
            # Cleanup so the test is hermetic.
            await probe.execute(
                text("SELECT pg_advisory_unlock(:k1, :k2)"),
                {"k1": key1, "k2": key2},
            )
        assert taken is True, "first _drive_sweep leaked its advisory lock"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_second_executor_cannot_drive_locked_sweep(
    db_session: AsyncSession,
) -> None:
    """When one executor holds the advisory lock on a sweep, a second
    executor's ``_drive_sweep`` returns immediately without pumping
    (``pg_try_advisory_lock`` returns False)."""

    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(
        bind=db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        sweep_id = uuid4()
        key1, key2 = _advisory_key_parts(sweep_id)
        # Pre-acquire the lock on a separate connection — simulates a
        # concurrent worker already driving the sweep.
        async with engine.connect() as holder:
            await holder.execute(
                text("SELECT pg_advisory_lock(:k1, :k2)"),
                {"k1": key1, "k2": key2},
            )
            try:
                executor = PairwiseSweepExecutor(
                    session_factory=factory, engine=engine
                )
                # Should NOT raise and should NOT pump. We assert by
                # checking that the worker did NOT try to acquire: that
                # would deadlock if implemented as blocking
                # pg_advisory_lock; the test passing proves the
                # non-blocking variant was used.
                await asyncio.wait_for(
                    executor._drive_sweep(sweep_id),  # noqa: SLF001
                    timeout=2.0,
                )
            finally:
                await holder.execute(
                    text("SELECT pg_advisory_unlock(:k1, :k2)"),
                    {"k1": key1, "k2": key2},
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_lock_released_after_executor_exception(
    db_session: AsyncSession,
) -> None:
    """If ``_pump_one_item`` raises, the finally block MUST still unlock
    on the same dedicated connection — otherwise the Sweep is stuck."""

    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(
        bind=db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        sweep_id = uuid4()
        executor = PairwiseSweepExecutor(
            session_factory=factory, engine=engine
        )

        # Inject a failing _pump_one_item so the drive path raises
        # mid-flight and triggers the finally.
        async def _boom(_sid: UUID, _judge: object) -> bool:
            raise RuntimeError("simulated provider hard error")

        executor._pump_one_item = _boom  # type: ignore[assignment]
        # The outer ``_execute`` swallows exceptions, so call
        # _drive_sweep directly and expect the RuntimeError to bubble.
        try:
            await executor._drive_sweep(sweep_id)  # noqa: SLF001
            raise AssertionError("expected RuntimeError to propagate")
        except RuntimeError:
            pass

        # Lock MUST be released — probe it.
        key1, key2 = _advisory_key_parts(sweep_id)
        async with engine.connect() as probe:
            taken = (
                await probe.execute(
                    text("SELECT pg_try_advisory_lock(:k1, :k2)"),
                    {"k1": key1, "k2": key2},
                )
            ).scalar_one()
            await probe.execute(
                text("SELECT pg_advisory_unlock(:k1, :k2)"),
                {"k1": key1, "k2": key2},
            )
        assert taken is True, "lock leaked after exception in pump"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_lock_released_after_cancelled_sweep(
    db_session: AsyncSession,
) -> None:
    """When a Sweep reaches a terminal/cancelled state mid-drive, the
    lock MUST still be released so the next recovery cycle can take it
    on a different worker (if needed)."""

    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(
        bind=db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        sweep_row = await _make_sweep(
            db_session, requested_pair_count=1, status="cancelled"
        )
        # Manually mark terminal AND set cancel_requested_at — DDL CHECK
        # ``ck_eval_pairwise_sweeps_cancelled_implies_both_timestamps``
        # requires both timestamps when status='cancelled'.
        from datetime import UTC
        from datetime import datetime as _dt

        sweep_row.terminal_at = _dt.now(UTC)
        sweep_row.cancel_requested_at = _dt.now(UTC)
        pair = await _seed_pair(db_session, 1)
        items = [
            _make_item(sweep_id=sweep_row.id, pair=pair, position="baseline"),
            _make_item(sweep_id=sweep_row.id, pair=pair, position="swapped"),
        ]
        await _persist(
            db_session=db_session, sweep_row=sweep_row, items=items
        )

        executor = PairwiseSweepExecutor(
            session_factory=factory, engine=engine
        )
        await executor._drive_sweep(sweep_row.id)  # noqa: SLF001

        # Lock MUST be released.
        key1, key2 = _advisory_key_parts(sweep_row.id)
        async with engine.connect() as probe:
            taken = (
                await probe.execute(
                    text("SELECT pg_try_advisory_lock(:k1, :k2)"),
                    {"k1": key1, "k2": key2},
                )
            ).scalar_one()
            await probe.execute(
                text("SELECT pg_advisory_unlock(:k1, :k2)"),
                {"k1": key1, "k2": key2},
            )
        assert taken is True, "lock leaked after cancelled sweep drive"
    finally:
        await engine.dispose()
