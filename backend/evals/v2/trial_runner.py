"""Real-Runtime TrialRunner for one V2 Eval Trial.

PR-3 minimal vertical slice. For each Trial the runner:

1. Marks the ``EvalTrial`` ``running``.
2. Asks the ``FixtureLoader`` to seed an isolated user (+ Profile, and for
   replan: a source Plan / Tasks / Review).
3. Builds a fully-injected Runtime (real ``AgentRunExecutor``, real
   ``build_tool_registry``, explicit ``MockPlanningProvider`` /
   ``MockEmbeddingProvider`` / ``MockSearchProvider``) per revision #4.
4. Creates the Run via ``AgentRunService.create`` (continue / create_plan) or
   ``ReviewService.start_next_plan`` (adjust). Services are given a
   ``_RecordingSubmitter`` so their ``executor.submit`` is a no-op -- the
   TrialRunner then drives ``executor.execute(run_id)`` itself.
5. Drives ``executor.execute`` under ``TerminalWaiter`` -- or, for the cancel
   case, races a cooperative ``AgentRunService.cancel`` once the planning node
   step has persisted (no arbitrary ``sleep``; revision #7).
6. Collects the post-terminal outcome from PostgreSQL via the collectors and
   freezes it on the ``EvalTrial``.

No Runtime logic is reimplemented here: every graph / node / tool / finalizer
call happens inside the real ``AgentRunExecutor``.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.executor import AgentRunExecutor
from app.core.config import Settings
from app.core.database import AsyncSessionFactory, session_transaction
from app.harness.provider_calls import (
    AuditEmbeddingProvider,
    AuditPlanningProvider,
    AuditSearchProvider,
    FixtureDesyncError,
    FixtureEmbeddingProvider,
    FixtureEntry,
    FixturePlanningProvider,
    FixtureSearchProvider,
    FixtureStore,
    LiveEvalCallController,
    LiveEvalRetryPolicy,
    ProviderCallRecorder,
    ProviderCallRepository,
    RetryAttemptState,
    RetryingEmbeddingProvider,
    RetryingPlanningProvider,
    RetryingSearchProvider,
)
from app.models.agent_run import AgentRun
from app.models.eval import EvalEvidenceItem, EvalTrial
from app.models.provider_call import EvalProviderFixtureItem
from app.prompts.career_planning import DIRECT_BASELINE_PROMPT_VERSION
from app.providers.embedding import MockEmbeddingProvider, build_embedding_provider
from app.providers.llm import (
    MockPlanningProvider,
    PairSmokePlanningProvider,
    build_planning_provider,
)
from app.providers.search import MockSearchProvider, build_search_provider
from app.repositories.agent_runs import AgentRunRepository
from app.repositories.evals import EvalRepository
from app.schemas.agent_runs import AgentRunCancelRequest
from app.services.agent_runs import AgentRunService
from app.services.reviews import ReviewService
from app.tools.registry import build_tool_registry
from evals.v2.collectors.evidence import collect_evidence
from evals.v2.collectors.outcome import RunOutcome, collect_outcome
from evals.v2.contracts import EvalCase, EvalScenario, PlanningEvalScenario
from evals.v2.experiment_runtime_context import ExperimentRuntimeContext
from evals.v2.fixture_loader import FixtureLoader
from evals.v2.scenario_adapter import RuntimeLaunch, adapt_scenario
from evals.v2.terminal_waiter import TerminalWaiter, WaitOutcome, WaitResult

# Marker constant kept in sync with ``MockPlanningProvider``.
_TIMEOUT_MARKER = "[mock:timeout]"


class _RecordingSubmitter(AgentRunExecutor):
    """No-op ``submit`` executor used for the Run-creation phase only.

    Real ``AgentRunExecutor.submit`` schedules ``execute`` as a background
    asyncio Task using the executor's own session factory. The TrialRunner
    instead calls ``executor.execute(run_id)`` itself (after the create
    transaction has committed) so it can apply the cancel race precisely.
    ``submit`` therefore only records the run id; it must never run the graph
    behind our back.
    """

    def __init__(self) -> None:
        # Provide a session factory that will never be used because both
        # ``submit`` and ``request_cancel`` are overridden to no-ops.
        super().__init__(AsyncSessionFactory)
        self.submitted: list[UUID] = []

    def submit(self, run_id: UUID) -> None:
        self.submitted.append(run_id)

    async def request_cancel(self, run_id: UUID) -> None:
        del run_id


@dataclass(frozen=True, slots=True)
class TrialRunnerConfig:
    deadline_seconds: float = 30.0


class TrialRunner:
    """Run one ``EvalTrial`` against the real Runtime."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        config: TrialRunnerConfig | None = None,
        runtime_context: ExperimentRuntimeContext | None = None,
    ) -> None:
        self._session_factory = session_factory
        # Revision #4: do not inherit Tool/Round defaults from the host .env.
        # ``AGENT_MAX_TOOL_ROUNDS=0`` would short-circuit every Case onto the
        # no-Tool ``generate_plan`` path; force the budget open so the Tool
        # smoke cases can exercise the registered handlers.
        self._settings = settings.model_copy(
            update={
                "agent_max_tool_rounds": 2,
                "agent_max_tool_calls": 4,
            }
        )
        self._config = config or TrialRunnerConfig(
            deadline_seconds=float(self._settings.agent_deadline_seconds) + 5.0
        )
        # Stage B-1a-lite: frozen experiment-level context for provider
        # selection. ``None`` = legacy path (all existing callers that
        # don't pass this param keep their current behavior).
        self._runtime_context = runtime_context
        self._live_call_controller = LiveEvalCallController(
            concurrency=self._settings.eval_live_concurrency,
            pacing_seconds=self._settings.eval_live_pacing_seconds,
        )
        self._live_retry_policy = LiveEvalRetryPolicy(
            max_attempts=self._settings.eval_live_max_attempts,
            base_seconds=self._settings.eval_live_retry_base_seconds,
            max_seconds=self._settings.eval_live_retry_max_seconds,
        )

    async def run_trial(
        self,
        trial: EvalTrial,
        case: EvalCase,
        *,
        fixture_source_trial_id: UUID | None = None,
    ) -> RunOutcome:
        """Execute one Trial end to end and freeze its terminal outcome."""

        replay_store, replay_bundle_hash = await self._load_fixture_replay_store(
            trial,
            fixture_source_trial_id=fixture_source_trial_id,
        )
        await self._mark_running(trial)
        scenario = case.scenario
        if not isinstance(scenario, PlanningEvalScenario):
            raise TypeError("standard TrialRunner only executes planning scenarios")
        # PR-8: capture provider_fixtures so the launched Run + ToolRegistry
        # honour per-Case counterfactual knobs (memory categories to exclude,
        # context compression budgets, available_tools allowlist, etc.).
        provider_fixtures = dict(scenario.provider_fixtures)
        user_id, source_plan_id, review_id = await self._seed_fixture(scenario)
        # PR-8 memory ablation: FixtureLoader.prepare_user plants
        # scenario.confirmed_memories (relevant / irrelevant / conflicting /
        # visible / hidden) inside the per-Trial user's row set. The category
        # in each Memory's content_json gates ``select_memories`` filtering.
        launch = adapt_scenario(
            scenario,
            trial_id=trial.id,
            source_plan_id=source_plan_id,
            review_id=review_id,
        )
        run_id = await self._launch_run(
            user_id, launch, provider_fixtures=dict(provider_fixtures)
        )
        record_store: FixtureStore | None = None
        active_store: FixtureStore | None
        if (
            replay_store is None
            and self._settings.eval_provider_mode == "fixture"
            and trial.run_type != "fixture_replay"
        ):
            # Recording mode: every normal Trial owns a new immutable bundle.
            record_store = FixtureStore(trial_id=trial.id)
            active_store = record_store
        else:
            active_store = replay_store
        executor = self._build_executor(
            trial_id=trial.id, run_id=run_id, fixture_store=active_store,
            provider_fixtures=dict(provider_fixtures),
        )
        try:
            wait = await self._drive_to_terminal(
                run_id=run_id, user_id=user_id, executor=executor, scenario=scenario
            )
        finally:
            await executor.close_resources()
        if replay_store is not None:
            try:
                replay_store.assert_fully_consumed()
            except FixtureDesyncError as exc:
                await self._fail_fixture_desync(
                    trial_id=trial.id,
                    run_id=run_id,
                    user_id=user_id,
                    error=exc,
                )
                raise
        if record_store is not None and record_store.entries_by_sequence:
            await self._persist_bundle(record_store)
        return await self._finalize_trial(
            trial.id,
            run_id,
            user_id,
            wait,
            case=case,
            fixture_source_trial_id=fixture_source_trial_id,
            fixture_bundle_hash=replay_bundle_hash,
        )

    async def _load_fixture_replay_store(
        self,
        trial: EvalTrial,
        *,
        fixture_source_trial_id: UUID | None,
    ) -> tuple[FixtureStore | None, str | None]:
        """Load the unique frozen recording selected by a fixture-replay Trial."""

        if trial.run_type != "fixture_replay":
            if fixture_source_trial_id is not None:
                raise RuntimeError(
                    "fixture_source_trial_id is only valid for fixture_replay Trials"
                )
            return None, None
        if self._settings.eval_provider_mode != "fixture":
            raise RuntimeError("fixture_replay Trial requires eval_provider_mode=fixture")
        if fixture_source_trial_id is None:
            raise RuntimeError("fixture_replay Trial requires fixture_source_trial_id")
        async with self._session_factory() as session:
            async with session_transaction(session):
                repo = ProviderCallRepository(session)
                source_trial = await session.get(EvalTrial, fixture_source_trial_id)
                if (
                    source_trial is None
                    or source_trial.status != "completed"
                    or source_trial.run_type == "fixture_replay"
                    or source_trial.case_fixture_hash != trial.case_fixture_hash
                ):
                    raise RuntimeError(
                        "fixture replay source must be a completed recording of the same fixture"
                    )
                bundles = await repo.list_bundles_for_trial(fixture_source_trial_id)
                if len(bundles) != 1:
                    raise RuntimeError("fixture replay source must own exactly one bundle")
                source_bundle = bundles[0]
                items = await repo.list_fixture_items(source_bundle.id)
                if len(items) != source_bundle.fixture_count:
                    raise RuntimeError("fixture replay bundle item count does not match metadata")
        store = FixtureStore(trial_id=trial.id)
        if any(item.response_payload_json is None for item in items):
            raise RuntimeError(
                "fixture replay source predates lossless response payloads; re-record it"
            )
        entries = [
            FixtureEntry(
                sequence=item.sequence,
                provider_kind=item.provider_kind,
                provider_method=item.provider_method,
                retry_attempt=item.retry_attempt,
                request_projection_hash=item.request_projection_hash,
                response_projection=item.response_projection,
                response_payload=item.response_payload_json,
                response_projection_hash=item.response_projection_hash,
                fixture_hash=item.fixture_hash,
            )
            for item in items
        ]
        store.freeze_for_replay(entries)
        computed_bundle_hash, computed_count = store.finalize_bundle_hash()
        if (
            computed_bundle_hash != source_bundle.bundle_hash
            or computed_count != source_bundle.fixture_count
        ):
            raise FixtureDesyncError(
                sequence=0,
                expected={
                    "bundle_hash": source_bundle.bundle_hash,
                    "fixture_count": source_bundle.fixture_count,
                },
                actual={
                    "bundle_hash": computed_bundle_hash,
                    "fixture_count": computed_count,
                },
                reason="fixture bundle metadata mismatch",
            )
        return store, source_bundle.bundle_hash

    async def _fail_fixture_desync(
        self,
        *,
        trial_id: UUID,
        run_id: UUID,
        user_id: UUID,
        error: FixtureDesyncError,
    ) -> None:
        """Persist an attributable Trial failure after a terminal Run drifts."""

        async with self._session_factory() as session:
            async with session_transaction(session):
                run = await AgentRunRepository(session).get_by_id(run_id)
                if run is None:
                    raise RuntimeError(f"run {run_id} disappeared")
                outcome = await collect_outcome(session, run, user_id=user_id)
                await EvalRepository(session).attach_trial_outcome(
                    trial_id,
                    status="failed",
                    run_id=run_id,
                    outcome_snapshot=None,
                    transcript_hash=None,
                    tokens_in=outcome.total_tokens_in,
                    tokens_out=outcome.total_tokens_out,
                    latency_ms=outcome.total_latency_ms,
                    finished_at=datetime.now(UTC),
                    error_code="FIXTURE_DESYNC",
                    error_message=str(error)[:1000],
                )

    async def _persist_bundle(self, store: FixtureStore) -> None:
        """Persist the record-mode bundle to DB so the next run for this Trial
        replays deterministically.
        """

        bundle_hash, fixture_count = store.finalize_bundle_hash()
        async with self._session_factory() as session:
            async with session_transaction(session):
                repo = ProviderCallRepository(session)
                bundle = await repo.create_bundle(
                    trial_id=store.trial_id,
                    bundle_hash=bundle_hash,
                    fixture_count=fixture_count,
                )
                items = []
                for entry in [
                    store.entries_by_sequence[s]
                    for s in sorted(store.entries_by_sequence)
                ]:
                    items.append(
                        EvalProviderFixtureItem(
                            bundle_id=bundle.id,
                            sequence=entry.sequence,
                            provider_kind=entry.provider_kind,
                            provider_method=entry.provider_method,
                            retry_attempt=entry.retry_attempt,
                            request_projection_hash=entry.request_projection_hash,
                            response_projection=entry.response_projection,
                            response_payload_json=entry.response_payload,
                            response_projection_hash=entry.response_projection_hash,
                            fixture_hash=entry.fixture_hash,
                        )
                    )
                await repo.create_fixture_items(items)

    async def _mark_running(self, trial: EvalTrial) -> None:
        async with self._session_factory() as session:
            async with session_transaction(session):
                await EvalRepository(session).mark_trial_running(
                    trial.id, started_at=datetime.now(UTC)
                )

    async def _seed_fixture(
        self, scenario: EvalScenario
    ) -> tuple[UUID, UUID | None, UUID | None]:
        async with self._session_factory() as session:
            async with session_transaction(session):
                fixture = FixtureLoader(session, self._settings)
                user_id = await fixture.prepare_user(scenario)
                source_plan_id: UUID | None = None
                review_id: UUID | None = None
                if scenario.hint_intent == "replan":
                    if scenario.replan_mode == "adjust":
                        plan, review = await fixture.seed_source_plan_for_adjust(
                            user_id,
                            adjustment_request=scenario.user_request,
                            blockers="reduced time budget this week",
                        )
                        source_plan_id = plan.id
                        review_id = review.id
                    else:
                        plan = await fixture.seed_source_plan_for_continue(user_id)
                        source_plan_id = plan.id
                return user_id, source_plan_id, review_id

    def _build_executor(
        self,
        *,
        trial_id: UUID,
        run_id: UUID,
        fixture_store: FixtureStore | None,
        provider_fixtures: dict[str, object] | None = None,
    ) -> AgentRunExecutor:
        """Build the executor for one Run, honouring ``settings.eval_provider_mode``.

        Three modes:
          • ``mock``    -- Mock providers + Audit wrapper (audit rows persisted).
          • ``fixture`` -- Mock providers + Fixture wrapper (lazy record + replay
                           on a 2nd Run with the same Trial, audit rows still
                           persisted via the recorder inside the Fixture wrapper).
          • ``live``    -- real providers with Eval-only bounded retry/pacing;
                           Audit remains enabled by default and records every
                           physical attempt with its ``retry_attempt`` index.

        ``self._settings`` already has the Tool budget forced open in ``__init__``.
        ``trial_id`` / ``run_id`` are needed so each ProviderCall row can be
        joinable from ``eval_trials``.
        """

        mode = self._settings.eval_provider_mode

        # Build the base (seed) providers; the wrappers below decorate them.
        base_planning: Any
        base_embedding: Any
        base_search: Any
        if mode == "live":
            agent_variant = (
                self._runtime_context.agent_variant
                if self._runtime_context is not None
                else None
            )
            base_planning = build_planning_provider(
                self._settings, agent_variant=agent_variant
            )
            base_embedding = build_embedding_provider(self._settings)
            base_search = build_search_provider(self._settings)
        else:  # mock or fixture
            # Stage B-1a-lite (Commit 3.5): experiment-level agent_variant
            # takes PRIORITY over the Stage-A global Settings profile.
            # When runtime_context is present and carries a non-null
            # agent_variant, build_planning_provider selects the
            # variant-specific deterministic provider.
            agent_variant = (
                self._runtime_context.agent_variant
                if self._runtime_context is not None
                else None
            )
            if agent_variant is not None:
                base_planning = build_planning_provider(
                    self._settings, agent_variant=agent_variant
                )
            else:
                # Stage A fallback: global Settings profile (unchanged).
                pair_smoke_profile = getattr(
                    self._settings, "eval_pair_smoke_planning_profile", None
                )
                if pair_smoke_profile is not None:
                    base_planning = PairSmokePlanningProvider(pair_smoke_profile)
                else:
                    base_planning = MockPlanningProvider()
            base_embedding = MockEmbeddingProvider()
            base_search = MockSearchProvider()

        planning_provider = base_planning
        embedding_provider = base_embedding
        search_provider = base_search

        # PR-9b: live mode also installs the ProviderCallRecorder so real
        # LLM / search / embedding calls leave an auditable, stat-able
        # trail. ``eval_audit_live_calls`` defaults True; operators can opt
        # out if they are streaming many trials through a low-token-cost
        # provider and don't want the ledger volume.
        recorder: ProviderCallRecorder | None = None
        if mode != "live":
            recorder = ProviderCallRecorder(
                session_factory=self._session_factory,
                run_id=run_id,
                trial_id=trial_id,
            )
            if mode == "fixture":
                # ``fixture_store`` is None on the first Run for a Trial
                # (lazy record path); a pre-populated store is supplied by
                # the TrialRunner when it re-runs a Trial whose bundle exists.
                store = fixture_store or FixtureStore(trial_id=trial_id)
                planning_provider = FixturePlanningProvider(
                    base_planning, recorder=recorder, store=store,
                )
                embedding_provider = FixtureEmbeddingProvider(
                    base_embedding, recorder=recorder, store=store,
                )
                search_provider = FixtureSearchProvider(
                    base_search, recorder=recorder, store=store,
                )
            else:  # mock wrapper only
                planning_provider = AuditPlanningProvider(
                    base_planning, recorder,
                )
                embedding_provider = AuditEmbeddingProvider(
                    base_embedding, recorder,
                )
                search_provider = AuditSearchProvider(
                    base_search, recorder,
                )
        else:
            planning_attempt = RetryAttemptState()
            embedding_attempt = RetryAttemptState()
            search_attempt = RetryAttemptState()
            if getattr(self._settings, "eval_audit_live_calls", True):
                recorder = ProviderCallRecorder(
                    session_factory=self._session_factory,
                    run_id=run_id,
                    trial_id=trial_id,
                    model_id=self._settings.llm_model,
                )
                planning_provider = AuditPlanningProvider(
                    base_planning,
                    recorder,
                    retry_attempt_getter=planning_attempt.get,
                )
                embedding_provider = AuditEmbeddingProvider(
                    base_embedding,
                    recorder,
                    retry_attempt_getter=embedding_attempt.get,
                )
                search_provider = AuditSearchProvider(
                    base_search,
                    recorder,
                    retry_attempt_getter=search_attempt.get,
                )
            planning_provider = RetryingPlanningProvider(
                planning_provider,
                controller=self._live_call_controller,
                policy=self._live_retry_policy,
                deadline_seconds=self._config.deadline_seconds,
                attempt_state=planning_attempt,
            )
            embedding_provider = RetryingEmbeddingProvider(
                embedding_provider,
                controller=self._live_call_controller,
                policy=self._live_retry_policy,
                deadline_seconds=self._config.deadline_seconds,
                attempt_state=embedding_attempt,
            )
            search_provider = RetryingSearchProvider(
                search_provider,
                controller=self._live_call_controller,
                policy=self._live_retry_policy,
                deadline_seconds=self._config.deadline_seconds,
                attempt_state=search_attempt,
            )

        tool_override = self._derive_tool_override(provider_fixtures)
        if self._is_direct_llm_variant():
            tool_override = set()
        tool_registry = build_tool_registry(
            settings=self._settings,
            session_factory=self._session_factory,
            embedding_provider=embedding_provider,
            search_provider=search_provider,
            available_tools_override=tool_override,
        )
        return AgentRunExecutor(
            session_factory=self._session_factory,
            provider=planning_provider,
            tool_registry=tool_registry,
            embedding_provider=embedding_provider,
            managed_resources=[base_planning, base_search],
        )

    async def _launch_run(
        self,
        user_id: UUID,
        launch: RuntimeLaunch,
        provider_fixtures: dict[str, object] | None = None,
    ) -> UUID:
        """Create the Agent Run via the real Runtime service.

        PR-8: ``provider_fixtures`` carries counterfactual knobs (memory
        categories to exclude, context compression budgets, tool allowlist,
        expected citations). When supplied, the persisted Run's
        ``config_snapshot_json`` is rewritten with those overrides AFTER
        AgentRunService.create (which writes the legacy snapshot) so the
        ContextBuilder downstream observes the per-Trial settings.
        """

        submitter = _RecordingSubmitter()
        async with self._session_factory() as session:
            async with session_transaction(session):
                if launch.kind == "review_start_next":
                    assert launch.review_id is not None
                    response = await ReviewService(
                        session, self._settings, submitter
                    ).start_next_plan(
                        review_id=launch.review_id,
                        user_id=user_id,
                        idempotency_key=launch.idempotency_suffix,
                    )
                    run_id = response.run_id
                else:
                    run = await AgentRunService(
                        session, self._settings, submitter
                    ).create(
                        user_id=user_id,
                        message=launch.message,
                        hint_intent=launch.hint_intent,
                        goal_type_override=None,
                        source_plan_id=launch.source_plan_id,
                        idempotency_key=launch.idempotency_suffix,
                    )
                    run_id = run.id
                if provider_fixtures:
                    await self._apply_counterfactual_overrides(
                        session, run_id, provider_fixtures
                    )
                if self._is_direct_llm_variant():
                    await self._apply_direct_llm_overrides(session, run_id)
                # Pre-trial arm invariant: fail fast on a misconfigured
                # arm BEFORE burning LLM budget on wrong-conclusion trials.
                from evals.v2.arm_invariants import check_pre_trial

                fresh = await session.get(AgentRun, run_id)
                violations = check_pre_trial(
                    self._runtime_context.agent_variant
                    if self._runtime_context
                    else None,
                    dict(fresh.config_snapshot_json or {}) if fresh else {},
                )
                if violations:
                    raise RuntimeError(
                        "arm configuration invariant violated: "
                        + "; ".join(f"{v.check}: {v.detail}" for v in violations)
                    )
                return run_id

    async def _apply_direct_llm_overrides(
        self,
        session: AsyncSession,
        run_id: UUID,
    ) -> None:
        run = await session.get(AgentRun, run_id, with_for_update=True)
        if run is None or run.config_snapshot_json is None:
            return
        snapshot = dict(run.config_snapshot_json)
        snapshot["available_tools"] = []
        raw_prompt_versions = snapshot.get("prompt_versions")
        prompt_versions = (
            dict(raw_prompt_versions) if isinstance(raw_prompt_versions, dict) else {}
        )
        prompt_versions["career_planning"] = DIRECT_BASELINE_PROMPT_VERSION
        snapshot["prompt_versions"] = prompt_versions
        run.config_snapshot_json = snapshot

    def _is_direct_llm_variant(self) -> bool:
        return bool(
            self._runtime_context is not None
            and self._runtime_context.agent_variant == "direct_llm_v1"
        )

    async def _apply_counterfactual_overrides(
        self,
        session: AsyncSession,
        run_id: UUID,
        provider_fixtures: dict[str, object],
    ) -> None:
        """Merge PR-8 counterfactual knobs into the Run's config snapshot.

        Only fields actually present in ``provider_fixtures`` are mutated;
        absent fields keep the default. ``available_tools`` (when present
        and a list) is normalized to ``set[str]`` for the ToolRegistry path,
        which is parameterised in ``_build_executor``.
        """

        run = await session.get(AgentRun, run_id, with_for_update=True)
        if run is None or run.config_snapshot_json is None:
            return
        snapshot = dict(run.config_snapshot_json)
        excluded = provider_fixtures.get("deferred_memory_categories")
        if isinstance(excluded, list):
            snapshot["exclude_memory_categories"] = [
                str(c) for c in excluded if isinstance(c, str)
            ]
        # PR-8b: ``pinned_memory_visibility="hidden"`` filters planted Memory
        # rows out of the planning catalog. FixtureLoader suffixes their
        # category with "__hidden" when planting; tell select_memories to
        # exclude those categories. The lookup Tool is unaffected.
        visibility = provider_fixtures.get("pinned_memory_visibility")
        if isinstance(visibility, str) and visibility == "hidden":
            existing_raw = snapshot.get("exclude_memory_categories")
            existing: list[str] = (
                [str(c) for c in existing_raw if isinstance(c, str)]
                if isinstance(existing_raw, list)
                else []
            )
            for base_cat in ("relevant", "irrelevant", "conflicting"):
                tag = f"{base_cat}__hidden"
                if tag not in existing:
                    existing.append(tag)
            snapshot["exclude_memory_categories"] = existing
        cc = provider_fixtures.get("context_compression")
        if isinstance(cc, dict):
            if isinstance(cc.get("recent_tasks_budget"), int):
                snapshot["context_recent_tasks_budget"] = int(cc["recent_tasks_budget"])
            if isinstance(cc.get("recent_reviews_budget"), int):
                snapshot["context_recent_reviews_budget"] = int(cc["recent_reviews_budget"])
        tools = provider_fixtures.get("available_tools")
        if isinstance(tools, list):
            snapshot["available_tools"] = [str(t) for t in tools]
            # The PR-8 ToolRegistry reads available_tools_override directly
            # (see _build_executor), but we still record it in available_tools
            # for snapshot readability.
        expected_citations = provider_fixtures.get("expected_citations")
        if isinstance(expected_citations, list):
            snapshot["expected_citations"] = [
                str(c) for c in expected_citations if isinstance(c, str)
            ]
        if "tool_required" in provider_fixtures:
            snapshot["tool_required"] = bool(provider_fixtures["tool_required"])
        run.config_snapshot_json = snapshot

    @staticmethod
    def _derive_tool_override(
        provider_fixtures: dict[str, object] | None,
    ) -> set[str] | None:
        """Translate the per-Case ``available_tools`` list into a ToolRegistry
        allowlist. None means "no override" (legacy behaviour); an empty set
        hides every tool from the model. The cf-tool-01 axis populates this
        field directly from its dataset payload.
        """

        if not provider_fixtures:
            return None
        if "available_tools" not in provider_fixtures:
            return None
        tools = provider_fixtures.get("available_tools")
        if not isinstance(tools, list):
            return None
        return {str(t) for t in tools if isinstance(t, str)}

    async def _drive_to_terminal(
        self,
        *,
        run_id: UUID,
        user_id: UUID,
        executor: AgentRunExecutor,
        scenario: EvalScenario,
    ) -> WaitResult:
        """Run ``executor.execute`` to terminal, or cancel-race for the timeout case."""

        if _TIMEOUT_MARKER in scenario.user_request:
            return await self._drive_cancel_race(
                run_id=run_id, user_id=user_id, executor=executor
            )

        waiter = TerminalWaiter(deadline_seconds=self._config.deadline_seconds)

        async def read_status() -> str | None:
            async with self._session_factory() as s:
                run = await AgentRunRepository(s).get_by_id(run_id)
                return run.status if run is not None else None

        async def request_cancel() -> None:
            await executor.request_cancel(run_id)

        return await waiter.await_terminal(
            executor.execute(run_id),
            run_id=run_id,
            read_status=read_status,
            request_cancel=request_cancel,
        )

    async def _drive_cancel_race(
        self,
        *,
        run_id: UUID,
        user_id: UUID,
        executor: AgentRunExecutor,
    ) -> WaitResult:
        """Cancel-then-execute path for ``[mock:timeout]`` cases.

        Original design: launch ``execute`` as a background Task and call
        ``AgentRunService.cancel`` once the planning node persisted, racing the
        LLM's ``asyncio.sleep(60)``. That form is correct in production
        (``submit`` schedules a real background Task on the process loop) but
        is not portable inside the per-test savepoint transaction fixture:
        a background ``execute`` re-enters the connection's savepoint stack
        after the outer test transaction has moved on, and asyncpg rejects the
        stale savepoint id, poisoning the test session.

        The same terminal invariant -- exactly one ``run.cancelled`` event
        with ``error_code=RUN_CANCELLED`` and no orphan active Run -- is
        produced deterministically by the executor's start gate: it observes
        ``cancel_requested_at`` and immediately finalizes cancelled without
        ever entering the LLM node (see ``executor.execute`` lines 92-94 and
        ``finalize_cancelled``). The cancel is therefore set first, then
        ``execute`` is awaited on the current task -- no background Task, no
        savepoint race.
        """

        # 1. Mark cancel-requested via the real Runtime service.
        async with self._session_factory() as s:
            async with session_transaction(s):
                await AgentRunService(
                    s, self._settings, executor
                ).cancel(
                    run_id=run_id,
                    user_id=user_id,
                    payload=AgentRunCancelRequest(),
                    idempotency_key=f"trial-cancel-{run_id}",
                )
        # 2. Drive the executor directly; its start gate detects
        #    ``cancel_requested_at`` and short-circuits to finalize_cancelled.
        await executor.execute(run_id)
        async with self._session_factory() as s:
            run = await AgentRunRepository(s).get_by_id(run_id)
            status = run.status if run is not None else None
        return WaitResult(outcome=WaitOutcome.COMPLETED, terminal_status=status)

    async def _finalize_trial(
        self,
        trial_id: UUID,
        run_id: UUID,
        user_id: UUID,
        wait: WaitResult,
        *,
        case: EvalCase,
        fixture_source_trial_id: UUID | None,
        fixture_bundle_hash: str | None,
    ) -> RunOutcome:
        async with self._session_factory() as session:
            async with session_transaction(session):
                runs = AgentRunRepository(session)
                run = await runs.get_by_id(run_id)
                if run is None:
                    raise RuntimeError(f"run {run_id} disappeared")
                outcome = await collect_outcome(session, run, user_id=user_id)
                repo = EvalRepository(session)
                await self._attach_outcome(
                    repo,
                    trial_id,
                    outcome,
                    wait,
                    fixture_source_trial_id=fixture_source_trial_id,
                    fixture_bundle_hash=fixture_bundle_hash,
                )
                if outcome.status in {"completed", "degraded"}:
                    items = await collect_evidence(
                        session,
                        trial_id=trial_id,
                        run=run,
                        outcome=outcome,
                        case=case,
                    )
                    await repo.delete_evidence_for_trial(trial_id)
                    await repo.create_evidence_items(
                        [
                            EvalEvidenceItem(
                                id=item.id,
                                trial_id=trial_id,
                                kind=item.kind.value,
                                source_type=item.source_type,
                                source_id=item.source_id,
                                content_hash=item.content_hash,
                                projection_json=item.projection,
                                sensitivity=item.sensitivity,
                            )
                            for item in items
                        ]
                    )
            return outcome

    async def _attach_outcome(
        self,
        repo: EvalRepository,
        trial_id: UUID,
        outcome: RunOutcome,
        wait: WaitResult,
        *,
        fixture_source_trial_id: UUID | None,
        fixture_bundle_hash: str | None,
    ) -> None:
        now = datetime.now(UTC)
        if outcome.status in {"completed", "degraded"}:
            snapshot: dict[str, object] = {
                "run": {
                    "id": str(outcome.run_id),
                    "user_id": str(outcome.user_id),
                    "status": outcome.status,
                    "result_kind": outcome.result_kind,
                    "final_plan_id": (
                        str(outcome.final_plan_id)
                        if outcome.final_plan_id is not None
                        else None
                    ),
                    "error_code": outcome.error_code,
                    "fallback_reason": outcome.fallback_reason,
                    "total_tokens_in": outcome.total_tokens_in,
                    "total_tokens_out": outcome.total_tokens_out,
                    "total_latency_ms": outcome.total_latency_ms,
                },
                "plan": outcome.plan,
                "tasks": outcome.tasks,
                "steps": outcome.steps,
                "events": outcome.events,
                "tool_calls": outcome.tool_calls,
                "provider_calls": outcome.provider_calls,
            }
            if fixture_source_trial_id is not None:
                snapshot["fixture_replay"] = {
                    "source_trial_id": str(fixture_source_trial_id),
                    "bundle_hash": fixture_bundle_hash,
                }
            await repo.attach_trial_outcome(
                trial_id,
                status="completed",
                run_id=outcome.run_id,
                outcome_snapshot=snapshot,
                transcript_hash=outcome.transcript_hash,
                tokens_in=outcome.total_tokens_in,
                tokens_out=outcome.total_tokens_out,
                latency_ms=outcome.total_latency_ms,
                finished_at=now,
                error_code=None,
            )
        else:
            await repo.attach_trial_outcome(
                trial_id,
                status="cancelled" if outcome.status == "cancelled" else "failed",
                run_id=outcome.run_id,
                outcome_snapshot=None,
                transcript_hash=None,
                tokens_in=outcome.total_tokens_in,
                tokens_out=outcome.total_tokens_out,
                latency_ms=outcome.total_latency_ms,
                finished_at=now,
                error_code=outcome.error_code or "RUN_NOT_COMPLETED",
                error_message=wait.outcome.value,
            )


def run_trial_factory(*, settings: Settings) -> TrialRunner:
    """Convenience constructor kept for explicit wiring in tests/scripts."""

    return TrialRunner(session_factory=AsyncSessionFactory, settings=settings)


# Suppress unused-import lint noise for AgentRun: the import documents the
# persisted type backing ``RunOutcome.run_id``.
_AGENT_RUN_TYPE = AgentRun  # noqa: F841
