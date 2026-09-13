"""Background executor for Eval V2 experiments.

Mirrors ``AgentRunExecutor``'s in-process ``asyncio.create_task`` discipline:
``EvalService.create_experiment`` (synchronously, in the request session)
inserts the ``EvalExperiment`` + ``EvalTrial`` rows, then the HTTP handler
calls ``submit()`` here; the spawned coroutine opens its own sessions via
``self._session_factory`` and drives ``ExperimentRunner.run_experiment_and_grade``.

No multi-worker reliability is claimed (same contract as ``AgentRunExecutor``).
Recovery on startup marks any ``running`` experiment ``failed`` because
``ExperimentRunner`` itself is stateless and cannot resume mid-Trial.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.database import AsyncSessionFactory, session_transaction
from app.core.exceptions import AppError
from app.harness.errors import EvalFailureCode
from app.models.eval import EvalExperiment
from app.services.evals import EvalService
from evals.v2.dataset_loader import DatasetBundle
from evals.v2.experiment_runner import ExperimentRunner

logger = logging.getLogger(__name__)

# Failure code stamped onto Trials stranded mid-flight when the process
# crashed and ``EvalRunnerExecutor.recover_interrupted`` runs.
_PROCESS_INTERRUPTED_CODE: str = EvalFailureCode.PROCESS_INTERRUPTED.value


class EvalRunnerExecutor:
    """Drives one Eval experiment per asyncio Task in-process."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] = AsyncSessionFactory,
        settings: Settings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or get_settings()
        self._tasks: dict[UUID, asyncio.Task[None]] = {}

    def submit(
        self, experiment_id: UUID, dataset: DatasetBundle, *, grade: bool = True
    ) -> None:
        """Spawn (or no-op if already running) the background coroutine."""

        current = self._tasks.get(experiment_id)
        if current is not None and not current.done():
            return
        task = asyncio.create_task(
            self._execute(experiment_id, dataset, grade=grade),
            name=f"eval-run-{experiment_id}",
        )
        self._tasks[experiment_id] = task
        task.add_done_callback(self._done_callback(experiment_id))

    async def _execute(
        self, experiment_id: UUID, dataset: DatasetBundle, *, grade: bool
    ) -> None:
        """Drive ``ExperimentRunner`` from a fresh session scope.

        ``run_experiment_and_grade`` transitions the experiment to ``failed``
        on execution or grading exceptions, so any exception reaching here
        has already been recorded in DB state. We still log the traceback so
        the asyncio default exception handler does not silently swallow it.
        """

        runner = ExperimentRunner(
            session_factory=self._session_factory, settings=self._settings
        )
        try:
            await runner.run_experiment_and_grade(experiment_id, dataset, grade=grade)
        except asyncio.CancelledError:
            async with self._session_factory() as session:
                await EvalService(session).finalize_cancelled_experiment(experiment_id)
        except Exception:
            logger.exception("eval experiment %s failed", experiment_id)

    async def request_cancel(self, experiment_id: UUID) -> None:
        """Cancel a running experiment's background task (if any)."""

        task = self._tasks.get(experiment_id)
        if task is not None and not task.done():
            task.cancel()

    async def recover_interrupted(self) -> int:
        """On process restart, mark every ``running`` experiment ``failed``.

        PR-9b: ``PROCESS_INTERRUPTED`` is stamped on the experiment row's
        non-terminal Trials so the failure-code taxonomy distinguishes a
        process crash from a runtime failure. ``cancel_requested_at`` does
        NOT override crash evidence: an experiment whose operator pressed
        cancel *and* whose process subsequently crashed still ends up in
        ``failed`` (with ``PROCESS_INTERRUPTED``); a clean operator cancel
        that survived would normally leave the experiment in ``cancelled``
        via the background task's finalize path (no crash).
        """

        async with self._session_factory() as session:
            async with session_transaction(session):
                rows = list(
                    (
                        await session.scalars(
                            select(EvalExperiment).where(
                                EvalExperiment.status == "running"
                            )
                        )
                    ).all()
                )
                service = EvalService(session)
                for exp in rows:
                    try:
                        await service.transition_experiment(exp.id, "failed")
                    except AppError:
                        # Illegal transition (rare race) -- never block startup.
                        continue
                    # Preserve the DB lifecycle contract during recovery:
                    # work that never started is cancelled, while in-flight
                    # work is failed. Both retain the interrupt reason.
                    trials = await service._evals.list_trials(exp.id)  # noqa: SLF001
                    for trial in trials:
                        if trial.status == "pending":
                            trial.status = "cancelled"
                        elif trial.status == "running":
                            trial.status = "failed"
                        else:
                            continue
                        if not trial.error_code:
                            trial.error_code = (
                                _PROCESS_INTERRUPTED_CODE
                            )
        return len(rows)

    async def shutdown(self) -> None:
        """Cancel and await every outstanding task on application shutdown."""

        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    def _discard(self, experiment_id: UUID, task: asyncio.Task[None]) -> None:
        if self._tasks.get(experiment_id) is task:
            self._tasks.pop(experiment_id, None)

    def _done_callback(
        self, experiment_id: UUID
    ) -> Callable[[asyncio.Task[None]], None]:
        def discard(task: asyncio.Task[None]) -> None:
            self._discard(experiment_id, task)

        return discard


eval_runner_executor = EvalRunnerExecutor()
