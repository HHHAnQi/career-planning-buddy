"""PostgreSQL-leased Agent Run scheduling and execution."""

import asyncio
import logging
import os
import socket
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.errors import (
    AgentDeadlineExceededError,
    AgentError,
    AgentLeaseLostError,
    RunCancelledError,
)
from app.agent.finalizer import AgentRunFinalizer
from app.agent.graph import GraphFactory, load_profile
from app.agent.interview_graph import InterviewGraph
from app.agent.node_runner import NodeRunner
from app.agent.resume_optimization_graph import ResumeOptimizationGraph
from app.core.database import AsyncSessionFactory, session_transaction
from app.core.telemetry import bind_telemetry_context
from app.harness.budget import BudgetGuard, CancellationToken
from app.harness.events import EventRecorder
from app.models.agent_run import AgentRun, AgentStep
from app.providers.embedding import EmbeddingProvider, MockEmbeddingProvider
from app.providers.evidence_distillation import (
    EvidenceDistillationProvider,
    MockEvidenceDistillationProvider,
)
from app.providers.interview import InterviewProvider, MockInterviewProvider
from app.providers.llm import MockPlanningProvider, PlanningProvider
from app.providers.resume_optimization import (
    MockResumeOptimizationProvider,
    ResumeOptimizationProvider,
)
from app.schemas.agent_runs import (
    PlanningState,
    RunRequestSnapshot,
    RuntimeConfigSnapshot,
)
from app.schemas.enums import GoalType, ReplanMode
from app.services.experience_atoms import ExperienceAtomService
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class AgentRunExecutor:
    """Claims durable Runs with leases and executes them with bounded concurrency."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = AsyncSessionFactory,
        provider: PlanningProvider | None = None,
        tool_registry: ToolRegistry | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        evidence_distillation_provider: EvidenceDistillationProvider | None = None,
        interview_provider: InterviewProvider | None = None,
        resume_optimization_provider: ResumeOptimizationProvider | None = None,
        managed_resources: list[object] | None = None,
        poll_interval_seconds: float = 0.25,
        heartbeat_seconds: float = 10,
        lease_seconds: float = 30,
        max_attempts: int = 3,
        worker_concurrency: int = 2,
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider or MockPlanningProvider()
        self._interview_provider = interview_provider or MockInterviewProvider()
        self._resume_optimization_provider = (
            resume_optimization_provider or MockResumeOptimizationProvider()
        )
        self._tool_registry = tool_registry or ToolRegistry()
        self._embedding_provider = embedding_provider or MockEmbeddingProvider()
        self._evidence_distillation_provider = (
            evidence_distillation_provider or MockEvidenceDistillationProvider()
        )
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._shutdown_run_ids: set[UUID] = set()
        self._managed_resources = list(managed_resources or [])
        self._poll_interval_seconds = poll_interval_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._worker_concurrency = worker_concurrency
        self._worker_id = (
            f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
        )
        self._wakeup = asyncio.Event()
        self._dispatcher_task: asyncio.Task[None] | None = None
        self._closing = False

    def submit(self, run_id: UUID) -> None:
        """Wake the durable dispatcher; PostgreSQL remains the queue of record."""
        del run_id
        self._wakeup.set()

    async def start(self) -> None:
        """Start one process-local consumer of the PostgreSQL lease queue."""
        if self._dispatcher_task is not None and not self._dispatcher_task.done():
            return
        self._closing = False
        self._wakeup = asyncio.Event()
        self._dispatcher_task = asyncio.create_task(
            self._dispatch_loop(),
            name=f"agent-dispatcher-{self._worker_id}",
        )
        self._wakeup.set()

    def configure_dispatcher(
        self,
        *,
        poll_interval_seconds: float,
        heartbeat_seconds: float,
        lease_seconds: float,
        max_attempts: int,
        worker_concurrency: int,
    ) -> None:
        if self._dispatcher_task is not None and not self._dispatcher_task.done():
            raise RuntimeError("cannot reconfigure a running Agent dispatcher")
        if lease_seconds <= heartbeat_seconds:
            raise ValueError("Agent lease must be longer than its heartbeat interval")
        self._poll_interval_seconds = poll_interval_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._worker_concurrency = worker_concurrency

    def set_provider(self, provider: PlanningProvider) -> None:
        """Configure the process-wide Provider before accepting new Runs."""
        if any(not task.done() for task in self._tasks.values()):
            raise RuntimeError("cannot replace Provider while Agent Runs are active")
        self._provider = provider

    def set_interview_provider(self, provider: InterviewProvider) -> None:
        if any(not task.done() for task in self._tasks.values()):
            raise RuntimeError("cannot replace Interview Provider while Runs are active")
        self._interview_provider = provider

    def set_resume_optimization_provider(
        self, provider: ResumeOptimizationProvider
    ) -> None:
        if any(not task.done() for task in self._tasks.values()):
            raise RuntimeError("cannot replace Resume Provider while Runs are active")
        self._resume_optimization_provider = provider

    def set_tool_registry(self, registry: ToolRegistry) -> None:
        if any(not task.done() for task in self._tasks.values()):
            raise RuntimeError("cannot replace Tool registry while Agent Runs are active")
        self._tool_registry = registry

    def set_embedding_provider(self, provider: EmbeddingProvider) -> None:
        if any(not task.done() for task in self._tasks.values()):
            raise RuntimeError("cannot replace Embedding Provider while Agent Runs are active")
        self._embedding_provider = provider

    def set_evidence_distillation_provider(self, provider: EvidenceDistillationProvider) -> None:
        if any(not task.done() for task in self._tasks.values()):
            raise RuntimeError("cannot replace Evidence Provider while Agent Runs are active")
        self._evidence_distillation_provider = provider

    async def execute(self, run_id: UUID) -> None:
        """Claim and execute one Run; retained as a deterministic test/CLI entrypoint."""
        loaded = await self._claim_by_id(run_id)
        if loaded is None:
            return
        run, config = loaded
        await self._execute_claimed(run, config)

    async def _execute_claimed(
        self,
        run: AgentRun,
        config: RuntimeConfigSnapshot | None,
    ) -> None:
        with bind_telemetry_context(
            trace_id=f"agent-run:{run.id}",
            run_id=str(run.id),
        ):
            await self._execute_claimed_in_context(run, config)

    async def _execute_claimed_in_context(
        self,
        run: AgentRun,
        config: RuntimeConfigSnapshot | None,
    ) -> None:
        run_id = run.id
        if config is None:
            await AgentRunFinalizer(
                self._session_factory,
                None,
                worker_id=self._worker_id,
                attempt_count=run.attempt_count,
            ).finalize_failed(
                run_id, error_code="CONFIG_SNAPSHOT_INVALID"
            )
            return
        cancellation = CancellationToken()
        owner_task = asyncio.current_task()
        if owner_task is None:
            raise RuntimeError("Agent Run execution requires an asyncio Task")
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(
                run_id,
                run.attempt_count,
                cancellation,
                owner_task,
            ),
            name=f"agent-heartbeat-{run_id}",
        )
        budget = BudgetGuard(config, run.deadline_at, cancellation)
        finalizer = AgentRunFinalizer(
            self._session_factory,
            budget,
            worker_id=self._worker_id,
            attempt_count=run.attempt_count,
        )
        runner = NodeRunner(
            self._session_factory,
            budget,
            config.node_timeouts_seconds,
            worker_id=self._worker_id,
            attempt_count=run.attempt_count,
        )
        try:
            if run.cancel_requested_at is not None:
                await finalizer.finalize_cancelled(run_id)
                return
            if run.run_kind.startswith("interview_"):
                if run.interview_session_id is None:
                    raise RuntimeError("Interview Run is missing its Session")
                await InterviewGraph(
                    session_factory=self._session_factory,
                    provider=self._interview_provider,
                    node_runner=runner,
                    finalizer=finalizer,
                    budget=budget,
                ).execute(
                    {
                        "run_id": run.id,
                        "user_id": run.user_id,
                        "run_kind": run.run_kind,
                        "interview_session_id": run.interview_session_id,
                        "interview_turn_id": run.interview_turn_id,
                    }
                )
                return
            if run.run_kind == "resume_optimization":
                if run.resume_version_id is None or run.job_target_id is None:
                    raise RuntimeError("Resume optimization Run is missing frozen materials")
                frozen = None
                if run.input_snapshot_json is not None:
                    from app.schemas.resumes import ResumeOptimizationInputSnapshot

                    frozen = ResumeOptimizationInputSnapshot.model_validate(
                        run.input_snapshot_json
                    )
                await ResumeOptimizationGraph(
                    session_factory=self._session_factory,
                    provider=self._resume_optimization_provider,
                    tool_registry=self._tool_registry,
                    node_runner=runner,
                    finalizer=finalizer,
                    budget=budget,
                    embedding_provider=self._embedding_provider,
                ).execute(
                    {
                        "run_id": run.id,
                        "user_id": run.user_id,
                        "interview_session_id": run.interview_session_id,
                        "resume_version_id": run.resume_version_id,
                        "job_target_id": run.job_target_id,
                        "attempt_count": run.attempt_count,
                        "replay_of_run_id": run.replay_of_run_id,
                        "replay_tool_fixture_only": config.replay_tool_mode == "fixture",
                        "replay_provider_fixture_only": (
                            config.execution_kind == "exact_fixture_replay"
                        ),
                        **({"input_snapshot": frozen} if frozen is not None else {}),
                    }
                )
                return
            graph = GraphFactory(
                self._session_factory,
                self._provider,
                self._tool_registry,
                self._embedding_provider,
            ).build(
                node_runner=runner,
                finalizer=finalizer,
                budget=budget,
            )
            profile = await load_profile(self._session_factory, run.user_id)
            state: PlanningState = {
                "run_id": run.id,
                "user_id": run.user_id,
                "request": RunRequestSnapshot(
                    message=run.request_text,
                    hint_intent=(
                        "replan"
                        if run.hint_intent == "replan"
                        else "create_plan"
                        if run.hint_intent == "create_plan"
                        else None
                    ),
                    goal_type_override=(
                        GoalType(run.goal_type_override)
                        if run.goal_type_override is not None
                        else None
                    ),
                    source_plan_id=run.source_plan_id,
                    source_review_id=run.source_review_id,
                    source_interview_report_session_id=(
                        run.source_interview_report_session_id
                    ),
                ),
                "runtime_config": config,
                "profile": profile,
                "server_replan_mode": (
                    ReplanMode(run.replan_mode)
                    if (
                        run.source_review_id is not None
                        or run.source_interview_report_session_id is not None
                    )
                    and run.replan_mode is not None
                    else None
                ),
                "repair_count": 0,
                "fallback_reason": None,
            }
            await graph.execute(state)
            if profile is not None:
                await self._distill_evidence_best_effort(run_id, profile.goal_type.value)
        except asyncio.CancelledError:
            cancellation.cancel()
            if run_id in self._shutdown_run_ids:
                await self._release_for_retry(
                    run_id,
                    attempt_count=run.attempt_count,
                    reason="graceful_shutdown",
                )
            else:
                await finalizer.finalize_cancelled(run_id)
        except AgentDeadlineExceededError:
            await finalizer.finalize_failed(run_id, error_code="AGENT_DEADLINE_EXCEEDED")
        except RunCancelledError:
            cancellation.cancel()
            await finalizer.finalize_cancelled(run_id)
        except AgentLeaseLostError:
            logger.info("agent run %s stopped after losing its lease", run_id)
        except AgentError as exc:
            await finalizer.finalize_failed(run_id, error_code=exc.code)
        except Exception:
            logger.exception("agent run %s failed unexpectedly", run_id)
            await finalizer.finalize_failed(run_id, error_code="AGENT_EXECUTION_FAILED")
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            if run_id not in self._shutdown_run_ids:
                try:
                    await self._ensure_terminal(run_id, finalizer)
                except AgentLeaseLostError:
                    pass

    async def request_cancel(self, run_id: UUID) -> None:
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()

    async def recover_interrupted(self) -> int:
        """Requeue expired leases and terminalize work that cannot be retried."""
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            async with session_transaction(session):
                run_ids = list(
                    await session.scalars(
                        select(AgentRun.id).where(
                            or_(
                                AgentRun.deadline_at <= now,
                                (
                                    (AgentRun.status == "running")
                                    & or_(
                                        AgentRun.lease_expires_at.is_(None),
                                        AgentRun.lease_expires_at <= now,
                                    )
                                ),
                            ),
                            AgentRun.status.in_(("pending", "running")),
                        )
                    )
                )
        processed = 0
        for run_id in run_ids:
            if await self._recover_candidate(run_id, observed_at=now):
                processed += 1
        if processed:
            self._wakeup.set()
        return processed

    async def shutdown(self) -> None:
        self._closing = True
        self._wakeup.set()
        dispatcher = self._dispatcher_task
        if dispatcher is not None and dispatcher is not asyncio.current_task():
            await asyncio.gather(dispatcher, return_exceptions=True)
        self._dispatcher_task = None
        active = [
            (run_id, task)
            for run_id, task in self._tasks.items()
            if not task.done()
        ]
        self._shutdown_run_ids.update(run_id for run_id, _ in active)
        tasks = [task for _, task in active]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._shutdown_run_ids.difference_update(run_id for run_id, _ in active)
        await self.close_resources()

    async def close_resources(self) -> None:
        resources = [
            self._provider,
            self._interview_provider,
            self._resume_optimization_provider,
            self._tool_registry,
            self._embedding_provider,
            self._evidence_distillation_provider,
            *self._managed_resources,
        ]
        closed: set[int] = set()
        for resource in resources:
            if id(resource) in closed:
                continue
            closed.add(id(resource))
            close = getattr(resource, "aclose", None)
            if callable(close):
                await close()
        self._managed_resources.clear()

    async def _claim_by_id(
        self, run_id: UUID
    ) -> tuple[AgentRun, RuntimeConfigSnapshot | None] | None:
        async with self._session_factory() as session:
            async with session_transaction(session):
                run = await session.scalar(
                    select(AgentRun).where(AgentRun.id == run_id).with_for_update()
                )
                if (
                    run is None
                    or run.status != "pending"
                    or run.deadline_at <= datetime.now(UTC)
                ):
                    return None
                return await self._claim_locked(session, run)

    async def _claim_next(
        self,
    ) -> tuple[AgentRun, RuntimeConfigSnapshot | None] | None:
        async with self._session_factory() as session:
            async with session_transaction(session):
                run = await session.scalar(
                    select(AgentRun)
                    .where(AgentRun.status == "pending")
                    .where(AgentRun.deadline_at > datetime.now(UTC))
                    .order_by(AgentRun.created_at, AgentRun.id)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                if run is None:
                    return None
                return await self._claim_locked(session, run)

    async def _claim_locked(
        self,
        session: AsyncSession,
        run: AgentRun,
    ) -> tuple[AgentRun, RuntimeConfigSnapshot | None]:
        now = datetime.now(UTC)
        run.status = "running"
        run.worker_id = self._worker_id
        run.heartbeat_at = now
        run.lease_expires_at = now + timedelta(seconds=self._lease_seconds)
        run.attempt_count += 1
        if run.started_at is None:
            run.started_at = now
        try:
            config = RuntimeConfigSnapshot.model_validate(run.config_snapshot_json)
        except ValidationError:
            config = None
        await session.flush()
        return run, config

    async def _dispatch_loop(self) -> None:
        last_recovery = 0.0
        loop = asyncio.get_running_loop()
        while not self._closing:
            try:
                now = loop.time()
                if now - last_recovery >= max(1.0, self._poll_interval_seconds):
                    await self.recover_interrupted()
                    last_recovery = now
                while (
                    not self._closing
                    and sum(not task.done() for task in self._tasks.values())
                    < self._worker_concurrency
                ):
                    loaded = await self._claim_next()
                    if loaded is None:
                        break
                    run, config = loaded
                    task = asyncio.create_task(
                        self._execute_claimed(run, config),
                        name=f"agent-run-{run.id}",
                    )
                    self._tasks[run.id] = task
                    task.add_done_callback(self._done_callback(run.id))
            except Exception:
                logger.exception("Agent Run dispatcher iteration failed")
            self._wakeup.clear()
            try:
                await asyncio.wait_for(
                    self._wakeup.wait(),
                    timeout=self._poll_interval_seconds,
                )
            except TimeoutError:
                pass

    async def _heartbeat_loop(
        self,
        run_id: UUID,
        attempt_count: int,
        cancellation: CancellationToken,
        owner_task: asyncio.Task[object],
    ) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            now = datetime.now(UTC)
            async with self._session_factory() as session:
                async with session_transaction(session):
                    result = await session.execute(
                        update(AgentRun)
                        .where(
                            AgentRun.id == run_id,
                            AgentRun.status == "running",
                            AgentRun.worker_id == self._worker_id,
                            AgentRun.attempt_count == attempt_count,
                        )
                        .values(
                            heartbeat_at=now,
                            lease_expires_at=now
                            + timedelta(seconds=self._lease_seconds),
                        )
                        .returning(AgentRun.id, AgentRun.cancel_requested_at)
                    )
                    row = result.one_or_none()
                    if row is None:
                        return
                    cancel_requested_at = row[1]
            if cancel_requested_at is not None:
                cancellation.cancel()
                owner_task.cancel()
                return

    async def _release_for_retry(
        self,
        run_id: UUID,
        *,
        attempt_count: int,
        reason: str,
    ) -> bool:
        async with self._session_factory() as session:
            async with session_transaction(session):
                run = await session.scalar(
                    select(AgentRun).where(AgentRun.id == run_id).with_for_update()
                )
                if (
                    run is None
                    or run.status != "running"
                    or run.worker_id != self._worker_id
                    or run.attempt_count != attempt_count
                ):
                    return False
                await session.execute(
                    update(AgentStep)
                    .where(AgentStep.run_id == run_id, AgentStep.status == "running")
                    .values(
                        status="failed",
                        error_code="PROCESS_INTERRUPTED",
                        error_message="PROCESS_INTERRUPTED",
                        finished_at=datetime.now(UTC),
                    )
                )
                run.status = "pending"
                run.worker_id = None
                run.lease_expires_at = None
                run.heartbeat_at = None
                await EventRecorder(session).record(
                    run_id,
                    "run.requeued",
                    {
                        "reason": reason,
                        "attempt": run.attempt_count,
                    },
                )
                return True

    async def _recover_candidate(self, run_id: UUID, *, observed_at: datetime) -> bool:
        """Recover one candidate after rechecking its lease under a row lock."""
        terminal_error: str | None = None
        async with self._session_factory() as session:
            async with session_transaction(session):
                run = await session.scalar(
                    select(AgentRun).where(AgentRun.id == run_id).with_for_update()
                )
                if run is None or run.status not in {"pending", "running"}:
                    return False
                now = datetime.now(UTC)
                if run.deadline_at <= now:
                    terminal_error = "AGENT_DEADLINE_EXCEEDED"
                elif (
                    run.status != "running"
                    or run.lease_expires_at is None
                    or run.lease_expires_at > observed_at
                ):
                    return False
                elif run.attempt_count >= self._max_attempts:
                    terminal_error = "AGENT_RETRY_EXHAUSTED"
                else:
                    await session.execute(
                        update(AgentStep)
                        .where(AgentStep.run_id == run_id, AgentStep.status == "running")
                        .values(
                            status="failed",
                            error_code="PROCESS_INTERRUPTED",
                            error_message="PROCESS_INTERRUPTED",
                            finished_at=now,
                        )
                    )
                    run.status = "pending"
                    run.worker_id = None
                    run.lease_expires_at = None
                    run.heartbeat_at = None
                    await EventRecorder(session).record(
                        run_id,
                        "run.requeued",
                        {"reason": "lease_expired", "attempt": run.attempt_count},
                    )
                    return True

                # Fence the expired owner before terminalization in a new transaction.
                run.status = "running"
                run.worker_id = self._worker_id
                run.heartbeat_at = now
                run.lease_expires_at = now + timedelta(seconds=self._lease_seconds)

        if terminal_error is None:
            return False
        return await AgentRunFinalizer(
            self._session_factory,
            None,
            worker_id=self._worker_id,
            attempt_count=None,
        ).finalize_failed(run_id, error_code=terminal_error)

    async def _ensure_terminal(self, run_id: UUID, finalizer: AgentRunFinalizer) -> None:
        async with self._session_factory() as session:
            async with session_transaction(session):
                status = await session.scalar(select(AgentRun.status).where(AgentRun.id == run_id))
        if status in {"pending", "running"}:
            await finalizer.finalize_failed(run_id, error_code="AGENT_TERMINAL_MISSING")

    async def _distill_evidence_best_effort(self, run_id: UUID, goal_type: str) -> None:
        try:
            async with self._session_factory() as session:
                await ExperienceAtomService(
                    session,
                    self._embedding_provider,
                    self._evidence_distillation_provider,
                ).distill_run(run_id=run_id, goal_type=goal_type)
        except Exception:
            logger.exception("evidence distillation failed for run %s", run_id)
            # Post-success enrichment must never alter the Run's terminal contract.
            return

    def _discard(self, run_id: UUID, task: asyncio.Task[None]) -> None:
        if self._tasks.get(run_id) is task:
            self._tasks.pop(run_id, None)

    def _done_callback(self, run_id: UUID) -> Callable[[asyncio.Task[None]], None]:
        def discard(task: asyncio.Task[None]) -> None:
            self._discard(run_id, task)
            self._wakeup.set()

        return discard


agent_run_executor = AgentRunExecutor()
