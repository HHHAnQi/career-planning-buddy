"""Fixed Stage 4 planning/replanning graph topology and node orchestration."""

import asyncio
import logging
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langsmith import tracing_context
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.context_compression import compress_context_history
from app.agent.context_selection import MemorySelectionResult
from app.agent.errors import StructuredOutputError
from app.agent.node_runner import NodeOutput, NodeRunner, NodeTelemetry
from app.agent.nodes import (
    build_clarification,
    build_companion,
    build_navigation,
    build_planning_context,
    build_safe_response,
    fallback_candidate,
    risk_gate,
    route_intent,
    validate_candidate,
)
from app.agent.ports import PlanningContextPort, PlanningResultPort
from app.agent.runtime_support import (
    as_int,
    build_provider_telemetry,
    candidate_checkpoint_payload,
    combine_provider_usage,
    extract_provider_usage,
    planning_input_fingerprint,
    restore_candidate_checkpoint,
)
from app.core.database import session_transaction
from app.core.metrics import AGENT_REPAIR_PATH, AGENT_UNKNOWN_RULE, make_labels
from app.harness.budget import BudgetGuard
from app.harness.checkpoints import CheckpointStore
from app.harness.events import EventRecorder
from app.harness.evidence import build_evidence_visibility
from app.harness.snapshots import SnapshotService
from app.harness.stream_progress import StreamProgressPublisher
from app.models.agent_run import AgentRun
from app.models.user_profile import UserProfile
from app.providers.embedding import EmbeddingProvider, MockEmbeddingProvider
from app.providers.llm import PlanningProvider
from app.providers.streaming import bind_stream_delta_sink
from app.schemas.agent_runs import (
    AgentTurnResponse,
    ClarificationRequest,
    EvidenceCatalogItem,
    EvidenceVisibility,
    HistoryParcel,
    IntentResult,
    MemoryContext,
    MemoryParcel,
    NavigationResult,
    PlanCandidate,
    PlanningContext,
    PlanningState,
    ProfileContext,
    ProviderPlanResponse,
    ProviderUsage,
    RunInputSnapshot,
    ValidationReport,
)
from app.schemas.enums import GoalType, ReplanMode, RunIntent
from app.tools.contracts import ToolContext
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class _NoOpLock:
    """Lock substitute for pooled (engine-bound) session factories."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_exc: object) -> None:
        return None


def _factory_binds_single_connection(
    session_factory: async_sessionmaker[AsyncSession],
) -> bool:
    """True when every session from this factory shares ONE connection.

    Engine-bound factories draw from the pool (safe to skip the loader
    lock); a factory bound directly to an AsyncConnection (test harness)
    multiplexes one wire and must serialize transactions.
    """

    bind = getattr(session_factory, "kw", {}).get("bind")
    from sqlalchemy.engine import Engine

    return not isinstance(bind, Engine)


class FixedPlanningGraph:
    """Explicit, bounded topology matching the Stage 2 runtime specification."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        provider: PlanningProvider,
        node_runner: NodeRunner,
        finalizer: PlanningResultPort,
        budget: BudgetGuard,
        tool_registry: ToolRegistry,
        embedding_provider: EmbeddingProvider,
        context_reader: PlanningContextPort,
    ) -> None:
        self._session_factory = session_factory
        self._checkpoints = CheckpointStore(session_factory)
        # Serializes DB transaction phases of the parallel context loaders
        # ONLY on single-connection runtimes (session factory bound to a
        # Connection instead of a pooled Engine): concurrent savepoint
        # transactions on one connection corrupt each other. On pooled
        # deployments the lock is skipped entirely — the decision is made
        # from the factory's bind type at construction time, not by policy.
        self._context_db_lock = (
            asyncio.Lock() if _factory_binds_single_connection(session_factory) else _NoOpLock()
        )
        self._provider = provider
        self._nodes = node_runner
        self._finalizer = finalizer
        self._budget = budget
        self._tool_registry = tool_registry
        self._embedding_provider = embedding_provider
        self._context_reader = context_reader
        self._graph = self._build_graph()

    async def execute(self, state: PlanningState) -> None:
        with tracing_context(enabled=False):
            await self._graph.ainvoke(state)

    def _build_graph(
        self,
    ) -> CompiledStateGraph[PlanningState, None, PlanningState, PlanningState]:
        builder = StateGraph(PlanningState)
        builder.add_node("risk_gate", self._risk_node)
        builder.add_node("safe_response", self._safe_response_node)
        builder.add_node("intent_router", self._intent_node)
        builder.add_node("navigation", self._navigation_node)
        builder.add_node("clarification", self._clarification_node)
        builder.add_node("memory_loader", self._memory_loader_node)
        builder.add_node("evidence_loader", self._evidence_loader_node)
        builder.add_node("context_builder", self._context_node)
        builder.add_node("career_planning_agent", self._agent_node)
        builder.add_node("rule_validator", self._validator_node)
        builder.add_node("revise_or_fallback", self._revise_node)
        builder.add_node("companion_response", self._companion_node)
        builder.add_node("persist", self._persist_node)
        builder.add_edge(START, "risk_gate")
        builder.add_conditional_edges(
            "risk_gate",
            self._after_risk,
            {"high": "safe_response", "safe": "intent_router"},
        )
        builder.add_edge("safe_response", END)
        # LangGraph native fan-out: the router returns BOTH loader nodes,
        # so they execute in the same superstep; context_builder joins
        # their parcels (each branch writes a disjoint state key).
        builder.add_conditional_edges("intent_router", self._route_after_intent)
        builder.add_edge("navigation", END)
        builder.add_edge("clarification", END)
        builder.add_edge("memory_loader", "context_builder")
        builder.add_edge("evidence_loader", "context_builder")
        builder.add_edge("context_builder", "career_planning_agent")
        builder.add_edge("career_planning_agent", "rule_validator")
        builder.add_conditional_edges(
            "rule_validator",
            self._after_validation,
            {"passed": "companion_response", "repair": "revise_or_fallback"},
        )
        builder.add_edge("revise_or_fallback", "rule_validator")
        builder.add_edge("companion_response", "persist")
        builder.add_edge("persist", END)
        return builder.compile()

    async def _risk_node(self, state: PlanningState) -> dict[str, object]:
        run_id = state["run_id"]
        request = state["request"]
        risk = await self._nodes.run(
            run_id,
            "risk_gate",
            lambda: self._immediate(
                risk_gate(request.message),
                {"method": "rule"},
            ),
        )
        await self._update_run(run_id, risk_category=risk.category)
        return {"risk": risk}

    async def _safe_response_node(self, state: PlanningState) -> dict[str, object]:
        risk = state["risk"]
        safe = await self._nodes.run(
            state["run_id"],
            "safe_response",
            lambda: self._immediate(build_safe_response(), {"category": risk.category}),
        )
        await self._finalizer.finalize_degraded(
            run_id=state["run_id"],
            result_kind="safe_response",
            result=safe,
            fallback_reason="high_risk_routed",
        )
        return {}

    async def _intent_node(self, state: PlanningState) -> dict[str, object]:
        run_id = state["run_id"]
        request = state["request"]
        source_plan_exists = request.source_plan_id is not None
        intent = await self._nodes.run(
            run_id,
            "intent_router",
            lambda: self._route_intent(
                message=request.message,
                hint_intent=request.hint_intent,
                profile=state["profile"],
                source_plan_exists=source_plan_exists,
                goal_type_override=request.goal_type_override,
                forced_replan_mode=state.get("server_replan_mode"),
            ),
        )
        await self._update_run(
            run_id,
            resolved_intent=intent.intent.value,
            replan_mode=intent.replan_mode.value,
            requested_horizon_weeks=intent.requested_horizon_weeks,
        )
        return {"intent": intent}

    @staticmethod
    async def _route_intent(
        *,
        message: str,
        hint_intent: str | None,
        profile: ProfileContext | None,
        source_plan_exists: bool,
        goal_type_override: GoalType | None,
        forced_replan_mode: ReplanMode | None,
    ) -> NodeOutput[IntentResult]:
        intent = route_intent(
            message=message,
            hint_intent=hint_intent,
            profile=profile,
            source_plan_exists=source_plan_exists,
            goal_type_override=goal_type_override,
            forced_replan_mode=forced_replan_mode,
        )
        return NodeOutput(
            intent,
            NodeTelemetry(
                trace_data={
                    "router_version": intent.router_version,
                    "method": intent.method,
                    "intent": intent.intent.value,
                    "replan_mode": intent.replan_mode.value,
                    "confidence": intent.confidence,
                    "confidence_band": intent.confidence_band,
                    "matched_rule_ids": intent.matched_rule_ids,
                    "ambiguity_reasons": intent.ambiguity_reasons,
                    "requested_horizon_weeks": intent.requested_horizon_weeks,
                    "missing_slots": intent.missing_slots,
                    "requires_fresh_information": intent.requires_fresh_information,
                }
            ),
        )

    async def _clarification_node(self, state: PlanningState) -> dict[str, object]:
        intent = state["intent"]
        clarification = await self._nodes.run(
            state["run_id"],
            "clarification",
            lambda: self._clarification_output(intent),
        )
        await self._finalizer.finalize_degraded(
            run_id=state["run_id"],
            result_kind="clarification",
            result=clarification,
            fallback_reason=clarification.reason,
        )
        return {}

    async def _navigation_node(self, state: PlanningState) -> dict[str, object]:
        intent = state["intent"]
        navigation = await self._nodes.run(
            state["run_id"],
            "navigation",
            lambda: self._navigation_output(intent),
        )
        await self._finalizer.finalize_degraded(
            run_id=state["run_id"],
            result_kind="navigation",
            result=navigation,
            fallback_reason="resource_navigation",
        )
        return {}

    @staticmethod
    async def _clarification_output(
        intent: IntentResult,
    ) -> NodeOutput[ClarificationRequest]:
        clarification = build_clarification(intent)
        return NodeOutput(
            clarification,
            NodeTelemetry(trace_data={"reason": clarification.reason}),
        )

    @staticmethod
    async def _navigation_output(intent: IntentResult) -> NodeOutput[NavigationResult]:
        navigation = build_navigation(intent)
        return NodeOutput(
            navigation,
            NodeTelemetry(
                trace_data={
                    "action": navigation.action,
                    "target_route": navigation.target_route,
                }
            ),
        )

    async def _context_node(self, state: PlanningState) -> dict[str, object]:
        """Join node: merges the memory_loader ∥ evidence_loader parcels,
        applies three-stage compression, and persists the input snapshot."""

        async def merge() -> NodeOutput[tuple[PlanningContext, list[EvidenceCatalogItem]]]:
            profile = state["profile"]
            intent = state["intent"]
            if profile is None or intent.effective_goal_type is None:
                raise StructuredOutputError(
                    "planning context requires a complete profile"
                )
            memory_parcel = state.get("memory_parcel") or MemoryParcel()
            history = state.get("history_parcel") or HistoryParcel()
            config = state["runtime_config"]
            context = build_planning_context(
                profile=profile,
                requested_horizon_weeks=intent.requested_horizon_weeks,
                source_plan_id=history.source_plan_id,
                source_plan_version=history.source_plan_version,
                source_plan=history.source_plan,
                source_review=history.source_review,
                recent_tasks=history.recent_tasks,
                recent_reviews=history.recent_reviews,
                completed_facts=history.completed_facts,
                blockers=history.blockers,
                planning_date=history.planning_date,
            )
            context = context.model_copy(
                update={
                    "pinned_memories": memory_parcel.selected,
                    "source_interview_report_session_id": (
                        state["request"].source_interview_report_session_id
                    ),
                    "interview_training_actions": history.interview_training_actions,
                }
            )
            # Hybrid relevance: embed the older tasks' surfaces (one
            # batched call) and reuse the memory branch's query vector so
            # synonym rewrites score semantically, not just lexically.
            # Best-effort: on any embedding failure the compressor falls
            # back to pure lexical scoring.
            task_vectors: dict[int, list[float]] | None = None
            older_surfaces = [
                " ".join(
                    part
                    for part in (task.deliverable, task.abandoned_reason_text or "")
                    if part
                )
                for task in history.recent_tasks[config.context_recent_tasks_budget:]
            ]
            if memory_parcel.query_vector is not None and older_surfaces:
                try:
                    embedded = await self._embedding_provider.embed(
                        older_surfaces[:32]
                    )
                    task_vectors = {
                        index: vector for index, vector in enumerate(embedded)
                    }
                except Exception:
                    task_vectors = None
            compression = compress_context_history(
                context,
                recent_tasks_budget=config.context_recent_tasks_budget,
                recent_reviews_budget=config.context_recent_reviews_budget,
                focus_query=state["request"].message,
                max_context_tokens=config.max_input_tokens_per_call,
                query_vector=memory_parcel.query_vector,
                task_vectors=task_vectors,
            )
            context = compression.context
            if compression.promoted_task_count or compression.budget_shrink_steps:
                logger.info(
                    "agent.context.compression",
                    extra={
                        "run_id": str(state["run_id"]),
                        "promoted_task_count": compression.promoted_task_count,
                        "budget_shrink_steps": compression.budget_shrink_steps,
                        "task_compressed_count": compression.task_compressed_count,
                        "before_chars": compression.before_chars,
                        "after_chars": compression.after_chars,
                    },
                )
            evidence_catalog = [
                EvidenceCatalogItem(
                    kind="memory",
                    id=memory.memory_id,
                    title=memory.memory_type,
                    content=memory.summary,
                    reliability=0.9,
                )
                for memory in memory_parcel.selected
            ]
            snapshot = RunInputSnapshot(
                profile=profile,
                planning_window=context.planning_window,
                source_plan_id=context.source_plan_id,
                source_plan_version=context.source_plan_version,
                source_plan=context.source_plan,
                source_review=context.source_review,
                source_interview_report_session_id=(
                    context.source_interview_report_session_id
                ),
                interview_training_actions=context.interview_training_actions,
                recent_tasks=context.recent_tasks,
                recent_reviews=context.recent_reviews,
                completed_facts=history.completed_facts,
                blockers=context.blockers,
                pinned_memories=context.pinned_memories,
                task_history_summary=context.task_history_summary,
                review_history_summary=context.review_history_summary,
                recent_task_ids=[task.task_id for task in history.recent_tasks],
                recent_review_ids=[
                    review.review_id for review in history.recent_reviews
                ],
                memory_versions={
                    str(memory.memory_id): memory.version
                    for memory in context.pinned_memories
                },
                timezone=context.timezone,
                time_budget_minutes=context.time_budget_minutes,
            )
            async with self._session_factory() as session:
                async with session_transaction(session):
                    await SnapshotService.write_input_once(
                        session, state["run_id"], snapshot
                    )
            return NodeOutput(
                (context, evidence_catalog),
                NodeTelemetry(
                    trace_data={
                        "token_estimate": context.token_estimate,
                        "horizon_weeks": context.planning_window.horizon_weeks,
                        "context_chars_before": compression.before_chars,
                        "context_chars_after": compression.after_chars,
                        "compressed_task_count": compression.task_compressed_count,
                        "compressed_review_count": compression.review_compressed_count,
                        "memory_query_hash": memory_parcel.query_hash,
                        "pinned_memory_count": memory_parcel.pinned_count,
                        "semantic_memory_count": memory_parcel.semantic_count,
                        "selected_memory_ids": [
                            str(item.memory_id) for item in memory_parcel.selected
                        ],
                        "memory_fallback_used": memory_parcel.fallback_used,
                        "memory_retrieval_failed": memory_parcel.retrieval_failed,
                        "memory_retrieval_latency_ms": memory_parcel.retrieval_latency_ms,
                    }
                ),
            )

        context, evidence_catalog = await self._nodes.run(
            state["run_id"],
            "context_builder",
            merge,
        )
        return {
            "planning_context": context,
            "evidence_catalog": evidence_catalog,
            "tool_round": 0,
            "tool_call_count": 0,
        }

    async def _agent_node(self, state: PlanningState) -> dict[str, object]:
        candidate, evidence_catalog, visibility, tool_round, tool_call_count = (
            await self._nodes.run_with_step(
                state["run_id"],
                "career_planning_agent",
                lambda step_id: self._generate_candidate(state, step_id),
            )
        )
        result: dict[str, object] = {
            "candidate_plan": candidate,
            "evidence_catalog": evidence_catalog,
            "candidate_evidence_visibility": visibility,
            "tool_round": tool_round,
            "tool_call_count": tool_call_count,
        }
        if state.get("fallback_reason") is not None:
            result["fallback_reason"] = state["fallback_reason"]
        if state.get("plan_provenance") is not None:
            result["plan_provenance"] = state["plan_provenance"]
        return result

    async def _validator_node(self, state: PlanningState) -> dict[str, object]:
        attempt = state.get("validation_attempt", 0) + 1
        validation = await self._nodes.run(
            state["run_id"],
            "rule_validator",
            lambda: self._validate_with_trace(
                state["candidate_plan"],
                state["planning_context"],
                state["candidate_evidence_visibility"],
                attempt,
            ),
            attempt=attempt,
        )
        return {
            "validation_report": validation,
            "validation_attempt": attempt,
        }

    async def _revise_node(self, state: PlanningState) -> dict[str, object]:
        candidate, fallback_reason, visibility = await self._nodes.run(
            state["run_id"],
            "revise_or_fallback",
            lambda: self._revise_or_fallback(state),
            attempt=state.get("repair_count", 0) + 1,
        )
        return {
            "candidate_plan": candidate,
            "candidate_evidence_visibility": visibility,
            "fallback_reason": fallback_reason,
            "repair_count": state.get("repair_count", 0) + 1,
            "plan_provenance": state.get("plan_provenance"),
            "unknown_rule_codes": state.get("unknown_rule_codes", []),
            "violation_category": state.get("violation_category"),
        }

    async def _companion_node(self, state: PlanningState) -> dict[str, object]:
        companion = await self._nodes.run(
            state["run_id"],
            "companion_response",
            lambda: self._immediate(
                build_companion(state["candidate_plan"]),
                {"template_version": "plan_ready_v1"},
            ),
        )
        return {"companion": companion}

    async def _persist_node(self, state: PlanningState) -> dict[str, object]:
        run_id = state["run_id"]
        provenance = self._derive_provenance(state)
        AGENT_REPAIR_PATH.inc(make_labels(path=provenance))
        # Durable attribution event: the eval attribution report joins this
        # against trial outcomes to split model vs engineering contribution.
        async with self._session_factory() as session:
            async with session_transaction(session):
                await EventRecorder(session).record(
                    run_id,
                    "run.provenance",
                    {
                        "plan_provenance": provenance,
                        "repair_count": state.get("repair_count", 0),
                        "validation_attempt": state.get("validation_attempt", 0),
                        "fallback_reason": state.get("fallback_reason"),
                        "unknown_rule_codes": state.get("unknown_rule_codes", []),
                        "violation_category": state.get("violation_category"),
                    },
                )
        persist_step = await self._nodes.start_step(run_id, "persist")
        await self._finalizer.finalize_plan(
            run_id=run_id,
            user_id=state["user_id"],
            candidate=state["candidate_plan"],
            evidence_visibility=state["candidate_evidence_visibility"],
            companion=state["companion"],
            persist_step_id=persist_step.id,
            fallback_reason=state.get("fallback_reason"),
            simulate_failure="[mock:persist-failure]" in state["request"].message,
        )
        return {}

    @staticmethod
    def _after_risk(state: PlanningState) -> str:
        return "high" if state["risk"].level == "high" else "safe"

    @staticmethod
    def _after_intent(state: PlanningState) -> str:
        intent = state["intent"]
        if intent.intent == RunIntent.NAVIGATE:
            return "navigation"
        if intent.intent == RunIntent.UNSUPPORTED or intent.missing_slots:
            return "clarification"
        return "ready"

    @staticmethod
    def _route_after_intent(state: PlanningState) -> str | list[str]:
        """Routing function for the conditional edge: returns node names
        (a list means fan-out — every listed node runs concurrently)."""
        branch = FixedPlanningGraph._after_intent(state)
        if branch == "ready":
            config = state.get("runtime_config")
            if config is not None and not config.context_fanout_enabled:
                # Serial A/B mode: only the evidence branch runs; it loads
                # memory inline inside its own lock section.
                return ["evidence_loader"]
            return ["memory_loader", "evidence_loader"]
        return branch

    @staticmethod
    def _after_validation(state: PlanningState) -> str:
        if state["validation_report"].passed or state.get("fallback_reason") is not None:
            return "passed"
        return "repair"

    async def _memory_loader_node(self, state: PlanningState) -> dict[str, object]:
        """Parallel branch 1: L2 Personal memory retrieval.

        Pre-phase (outside the recorded step): a short locked read for the
        query context, then the UNLOCKED embedding call — the network-bound
        part that genuinely overlaps evidence_loader's DB work. The
        recorded step then runs exclusively under the shared context lock.
        """
        from time import monotonic

        from app.agent.context_selection import build_memory_query

        run_id = state["run_id"]
        intent = state["intent"]
        config = state["runtime_config"]
        if config.memory_disabled or intent.effective_goal_type is None:
            return {"memory_parcel": MemoryParcel()}
        goal_type = intent.effective_goal_type

        async with self._context_db_lock:
            query_facts = await self._context_reader.load_memory_query_facts(
                user_id=state["user_id"],
                source_review_id=state["request"].source_review_id,
            )
        query = build_memory_query(
            user_message=state["request"].message,
            goal_type=intent.effective_goal_type.value,
            blockers=query_facts.blockers,
            adjustment_request=query_facts.adjustment_request,
        )
        vector: list[float] | None = None
        embed_latency_ms = 0
        if config.memory_semantic_retrieval_enabled and query:
            try:
                _embed_started = monotonic()
                vectors = await self._embedding_provider.embed([query])
                vector = vectors[0] if vectors else None
                embed_latency_ms = int((monotonic() - _embed_started) * 1000)
            except Exception:
                vector = None

        async def work() -> NodeOutput[dict[str, object]]:
            started = monotonic()
            memory_selection = MemorySelectionResult(
                selected=[],
                query_hash="",
                pinned_count=0,
                semantic_count=0,
                fallback_used=False,
                retrieval_failed=False,
            )
            try:
                memory_selection = await self._context_reader.select_memories(
                    user_id=state["user_id"],
                    user_message=state["request"].message,
                    goal_type=goal_type.value,
                    blockers=query_facts.blockers,
                    adjustment_request=query_facts.adjustment_request,
                    semantic_enabled=config.memory_semantic_retrieval_enabled,
                    retrieval_limit=config.memory_retrieval_limit,
                    max_items=config.memory_context_max_items,
                    max_chars=config.memory_context_max_chars,
                    min_similarity=config.memory_min_similarity,
                    half_life_days=config.memory_recency_half_life_days,
                    exclude_categories=(
                        set(config.exclude_memory_categories)
                        if config.exclude_memory_categories
                        else None
                    ),
                    precomputed_vector=vector,
                )
            except Exception:
                memory_selection = memory_selection.__class__(
                    selected=[],
                    query_hash=memory_selection.query_hash,
                    pinned_count=0,
                    semantic_count=0,
                    fallback_used=memory_selection.fallback_used,
                    retrieval_failed=True,
                )
            parcel = MemoryParcel(
                selected=[
                    MemoryContext(
                        memory_id=memory.memory_id,
                        version=memory.version,
                        memory_type=memory.memory_type,
                        summary=memory.summary,
                    )
                    for memory in memory_selection.selected
                ],
                query_hash=memory_selection.query_hash,
                pinned_count=memory_selection.pinned_count,
                semantic_count=memory_selection.semantic_count,
                fallback_used=memory_selection.fallback_used,
                retrieval_failed=memory_selection.retrieval_failed,
                retrieval_latency_ms=int((monotonic() - started) * 1000),
                embed_latency_ms=embed_latency_ms,
                query_vector=vector,
            )
            return await self._immediate(
                {"memory_parcel": parcel},
                {
                    "selected_memory_count": len(parcel.selected),
                    "pinned_count": parcel.pinned_count,
                    "semantic_count": parcel.semantic_count,
                    "fallback_used": parcel.fallback_used,
                    "retrieval_failed": parcel.retrieval_failed,
                    "retrieval_latency_ms": parcel.retrieval_latency_ms,
                    "embed_latency_ms": parcel.embed_latency_ms,
                },
            )

        return await self._nodes.run_exclusive(
            run_id,
            "memory_loader",
            work,
            lock=self._context_db_lock,
        )

    async def _evidence_loader_node(self, state: PlanningState) -> dict[str, object]:
        """Parallel branch 2: source plan/review, task history, interview
        actions — every DB read the planning context needs except memory.
        Runs exclusively under the shared context lock so the two fan-out
        branches never interleave transactions on a single connection."""

        async def work() -> NodeOutput[dict[str, object]]:
            parcel = await self._context_reader.load_history(
                user_id=state["user_id"],
                source_plan_id=state["request"].source_plan_id,
                source_review_id=state["request"].source_review_id,
                source_interview_report_session_id=(
                    state["request"].source_interview_report_session_id
                ),
                request_message=state["request"].message,
            )
            if not state["runtime_config"].context_fanout_enabled:
                # Serial A/B mode: this branch also produces the memory
                # parcel so the merge node is unchanged.
                memory_result = await self._memory_loader_node(state)
                return await self._immediate(
                    {
                        "history_parcel": parcel,
                        "memory_parcel": memory_result["memory_parcel"],
                    },
                    {"serial_context_mode": True},
                )
            return await self._immediate(
                {"history_parcel": parcel},
                {
                    "source_plan_present": parcel.source_plan is not None,
                    "recent_task_count": len(parcel.recent_tasks),
                    "recent_review_count": len(parcel.recent_reviews),
                    "interview_action_count": len(parcel.interview_training_actions),
                },
            )

        return await self._nodes.run_exclusive(
            state["run_id"],
            "evidence_loader",
            work,
            lock=self._context_db_lock,
        )

    async def _generate_candidate(
        self,
        state: PlanningState,
        step_id: UUID,
    ) -> NodeOutput[
        tuple[PlanCandidate, list[EvidenceCatalogItem], EvidenceVisibility, int, int]
    ]:
        """Checkpointed wrapper around the expensive planning generation.

        Recovery semantics (mirrors the resume optimization graph): a
        checkpoint saved by an earlier attempt of this Run is reused iff
        the planning input fingerprint matches — the prompt inputs are
        immutable per Run, so a fingerprint mismatch means tampering or
        schema drift and the checkpoint is ignored. Restoring skips the
        provider calls entirely (the expensive part); validation and the
        deterministic downstream nodes re-run normally.
        """

        fingerprint = planning_input_fingerprint(
            state["request"].message,
            state["planning_context"],
            state["intent"].replan_mode,
        )
        frozen = await self._checkpoints.load(
            state["run_id"], "career_planning_agent"
        )
        if frozen is not None and frozen.get("input_hash") == fingerprint:
            restored = restore_candidate_checkpoint(frozen)
            if restored is not None:
                if restored.fallback_reason is not None:
                    state["fallback_reason"] = restored.fallback_reason
                return restored.output
            logger.debug(
                "career_planning_agent checkpoint rejected for run %s",
                state["run_id"],
            )

        output = await self._generate_candidate_uncached(state, step_id)
        try:
            payload = {
                **candidate_checkpoint_payload(output, state),
                "input_hash": fingerprint,
            }
            await self._checkpoints.save(
                state["run_id"],
                as_int(state.get("attempt_count"), 1),
                "career_planning_agent",
                payload,
            )
        except Exception:  # noqa: BLE001 - checkpointing is best-effort
            logger.debug(
                "career_planning_agent checkpoint save failed for run %s",
                state["run_id"],
                exc_info=True,
            )
        return output

    async def _generate_candidate_uncached(
        self,
        state: PlanningState,
        step_id: UUID,
    ) -> NodeOutput[
        tuple[PlanCandidate, list[EvidenceCatalogItem], EvidenceVisibility, int, int]
    ]:
        context = state["planning_context"]
        mode = state["intent"].replan_mode
        evidence_catalog = list(state.get("evidence_catalog", []))
        tool_round = state.get("tool_round", 0)
        tool_call_count = state.get("tool_call_count", 0)
        total_usage: ProviderUsage | None = None
        stream_summaries: list[dict[str, object]] = []
        prompt_version = state["runtime_config"].prompt_versions["career_planning"]

        def _step_telemetry(visibility: EvidenceVisibility) -> NodeTelemetry:
            # Every return path below follows at least one provider call,
            # so total_usage is always bound by the time this runs.
            assert total_usage is not None
            telemetry = build_provider_telemetry(total_usage, prompt_version, visibility)
            if stream_summaries:
                telemetry.trace_data["llm_stream"] = stream_summaries
            return telemetry

        for turn_index in range(3):
            force_final = (
                tool_round >= state["runtime_config"].max_tool_rounds
                or tool_call_count >= state["runtime_config"].max_tool_calls
            )
            available_tools = self._tool_registry.available_specs(
                intent=state["intent"].intent,
                requires_fresh_information=state["intent"].requires_fresh_information,
            )
            # Deterministic memory pre-execution: when the router detects
            # past-context references, EXECUTE memory_lookup directly (not
            # just make it available — the model won't call it voluntarily,
            # measured ~11% on GLM-4.7). Results go into the evidence
            # catalog before the LLM call, so the tool_calls table records
            # the execution and the grader sees it.
            if (
                turn_index == 0
                and not force_final
                and not state["runtime_config"].memory_disabled
                and state["intent"].references_past_context
                and state["intent"].intent in {RunIntent.CREATE_PLAN, RunIntent.REPLAN}
                and not any(
                    item.kind == "memory"
                    for item in evidence_catalog
                )
            ):
                pre_execution = await self._tool_registry.execute(
                    tool_name="memory_lookup",
                    arguments={"query": state["request"].message[:200], "limit": 5},
                    context=ToolContext(
                        run_id=state["run_id"],
                        user_id=state["user_id"],
                        goal_type=context.profile.goal_type,
                        intent=state["intent"].intent,
                        requires_fresh_information=(
                            state["intent"].requires_fresh_information
                        ),
                        remaining_deadline_ms=int(self._budget.remaining_seconds() * 1000),
                    ),
                    step_id=step_id,
                    round_number=1,
                )
                tool_call_count += 1
                if pre_execution.success:
                    for item in pre_execution.result.evidence:
                        catalog_item = EvidenceCatalogItem.model_validate(
                            item.model_dump(mode="json")
                        )
                        if all(
                            existing.kind != catalog_item.kind
                            or existing.id != catalog_item.id
                            for existing in evidence_catalog
                        ):
                            evidence_catalog.append(catalog_item)
            if force_final:
                available_tools = []
            visible_catalog, visibility = build_evidence_visibility(
                call_id=f"{state['run_id']}:career_planning:{turn_index + 1}",
                evidence_catalog=evidence_catalog,
            )
            # 当本轮没有任何 Tool 时，走更轻量的 generate_plan prompt 路径
            # （ProviderPlanResponse schema）。带 Tool 时才使用完整的 agent-turn prompt。
            using_plan_path = not available_tools
            # Wire-level streaming: bind one throttled progress publisher per
            # LLM call. Mock/deterministic providers never call the sink, so
            # evaluation determinism is unchanged.
            publisher = StreamProgressPublisher(
                session_factory=self._session_factory,
                run_id=state["run_id"],
                node_name="career_planning_agent",
                turn=turn_index + 1,
            )
            with bind_stream_delta_sink(publisher.on_delta):
                if available_tools:
                    raw = await self._provider.generate_agent_turn(
                        message=state["request"].message,
                        context=context,
                        replan_mode=mode,
                        available_tools=available_tools,
                        evidence_catalog=visible_catalog,
                        force_final=force_final,
                    )
                else:
                    raw = await self._provider.generate_plan(
                        message=state["request"].message,
                        context=context,
                        replan_mode=mode,
                        evidence_catalog=visible_catalog,
                    )
            stream_summaries.append(publisher.summary())
            usage = extract_provider_usage(raw)
            self._budget.record_llm_call(usage.tokens_in, usage.tokens_out)
            total_usage = (
                usage if total_usage is None else combine_provider_usage(total_usage, usage)
            )
            try:
                if using_plan_path:
                    response = ProviderPlanResponse.model_validate(raw)
                    candidate = response.candidate
                    return NodeOutput(
                        (
                            candidate,
                            evidence_catalog,
                            visibility,
                            tool_round,
                            tool_call_count,
                        ),
                        _step_telemetry(visibility),
                    )
                turn = AgentTurnResponse.model_validate(raw)
            except ValidationError:
                self._budget.claim_format_repair()
                repair_catalog, repair_visibility = build_evidence_visibility(
                    call_id=f"{state['run_id']}:format_repair",
                    evidence_catalog=evidence_catalog,
                )
                repaired = await self._provider.repair_format(
                    raw_output=raw,
                    context=context,
                    replan_mode=mode,
                    evidence_catalog=repair_catalog,
                )
                repair_usage = extract_provider_usage(repaired)
                self._budget.record_llm_call(
                    repair_usage.tokens_in,
                    repair_usage.tokens_out,
                )
                total_usage = combine_provider_usage(total_usage, repair_usage)
                prompt_version = state["runtime_config"].prompt_versions["format_repair"]
                try:
                    response = ProviderPlanResponse.model_validate(repaired)
                except ValidationError:
                    state["fallback_reason"] = "format_repair_failed"
                    fallback = fallback_candidate(context, mode)
                    _, fallback_visibility = build_evidence_visibility(
                        call_id=f"{state['run_id']}:format_fallback",
                        evidence_catalog=[],
                    )
                    return NodeOutput(
                        (
                            fallback,
                            evidence_catalog,
                            fallback_visibility,
                            tool_round,
                            tool_call_count,
                        ),
                        _step_telemetry(repair_visibility),
                    )
                # Format repair succeeded: the finalized candidate came from
                # the provider repair call, not the model's first-shot output.
                state["plan_provenance"] = "format_repair"
                return NodeOutput(
                    (
                        response.candidate,
                        evidence_catalog,
                        repair_visibility,
                        tool_round,
                        tool_call_count,
                    ),
                    _step_telemetry(repair_visibility),
                )
            if turn.final is not None:
                return NodeOutput(
                    (
                        turn.final,
                        evidence_catalog,
                        visibility,
                        tool_round,
                        tool_call_count,
                    ),
                    _step_telemetry(visibility),
                )
            if force_final:
                raise StructuredOutputError("Tool requested after Tool budget was exhausted")
            tool_round += 1
            # Parallel tool calls: every accepted call in one round runs
            # concurrently (the registry opens its own session per call).
            # gather preserves submission order, so evidence collection and
            # mock determinism are unchanged; a tool failure arrives as a
            # non-successful ToolExecutionResult, never as an exception.
            remaining_calls = (
                state["runtime_config"].max_tool_calls - tool_call_count
            )
            accepted = [call for call in turn.tool_calls[:remaining_calls]]
            tool_call_count += len(accepted)
            if accepted:
                deadline_ms = int(self._budget.remaining_seconds() * 1000)
                tool_context = ToolContext(
                    run_id=state["run_id"],
                    user_id=state["user_id"],
                    goal_type=context.profile.goal_type,
                    intent=state["intent"].intent,
                    requires_fresh_information=(state["intent"].requires_fresh_information),
                    remaining_deadline_ms=deadline_ms,
                )
                executions = await asyncio.gather(
                    *(
                        self._tool_registry.execute(
                            tool_name=call.name,
                            arguments=call.arguments,
                            context=tool_context,
                            step_id=step_id,
                            round_number=tool_round,
                        )
                        for call in accepted
                    )
                )
                for execution in executions:
                    if execution.success:
                        for item in execution.result.evidence:
                            catalog_item = EvidenceCatalogItem.model_validate(
                                item.model_dump(mode="json")
                            )
                            if all(
                                existing.kind != catalog_item.kind
                                or existing.id != catalog_item.id
                                for existing in evidence_catalog
                            ):
                                evidence_catalog.append(catalog_item)
        if total_usage is None:
            raise StructuredOutputError("Agent produced no turn")
        state["fallback_reason"] = "tool_round_exhausted"
        fallback = fallback_candidate(context, mode)
        _, fallback_visibility = build_evidence_visibility(
            call_id=f"{state['run_id']}:tool_fallback",
            evidence_catalog=[],
        )
        return NodeOutput(
            (
                fallback,
                evidence_catalog,
                fallback_visibility,
                tool_round,
                tool_call_count,
            ),
            _step_telemetry(fallback_visibility),
        )

    async def _revise_or_fallback(
        self, state: PlanningState
    ) -> NodeOutput[tuple[PlanCandidate, str | None, EvidenceVisibility]]:
        context = state["planning_context"]
        validation = state["validation_report"]
        failed_codes = [
            check.code for check in validation.checks if not check.passed
        ]

        # Deterministic repair first: fix rule violations in code before
        # burning an LLM call on a repair the model can't do (live eval:
        # GLM-4.7 business-repair success is 0/6). If code can fix it,
        # the repaired candidate goes straight to re-validation.
        from app.agent.deterministic_repair import (
            DETERMINISTICALLY_REPAIRABLE,
            deterministic_repair,
        )

        unknown_codes = [
            code for code in failed_codes if code not in DETERMINISTICALLY_REPAIRABLE
        ]
        if unknown_codes:
            # Unknown-rule backlog: codes the deterministic layer cannot
            # patch are logged and counted so the offline rule-iteration
            # loop can promote recurring patterns into code repairs.
            state["unknown_rule_codes"] = unknown_codes
            logger.warning(
                "agent.repair.unknown_rule",
                extra={
                    "run_id": str(state["run_id"]),
                    "unknown_rule_codes": unknown_codes,
                    "failed_checks": failed_codes,
                },
            )
            for code in unknown_codes:
                AGENT_UNKNOWN_RULE.inc(make_labels(code=code))

        deterministically_fixed = deterministic_repair(
            state["candidate_plan"], context, failed_codes
        )
        if deterministically_fixed is not None:
            recheck = validate_candidate(deterministically_fixed, context)
            if recheck.passed:
                _, visibility = build_evidence_visibility(
                    call_id=f"{state['run_id']}:deterministic_repair",
                    evidence_catalog=list(state.get("evidence_catalog", [])),
                )
                AGENT_REPAIR_PATH.inc(make_labels(path="deterministic"))
                state["plan_provenance"] = "deterministic_repair"
                return NodeOutput(
                    (deterministically_fixed, None, visibility),
                )

        if not self._budget.can_reserve_llm_call():
            fallback = fallback_candidate(context, state["intent"].replan_mode)
            _, visibility = build_evidence_visibility(
                call_id=f"{state['run_id']}:business_budget_fallback",
                evidence_catalog=[],
            )
            return NodeOutput(
                (fallback, "business_repair_budget_insufficient", visibility)
            )
        if not self._budget.claim_business_repair():
            fallback = fallback_candidate(context, state["intent"].replan_mode)
            _, visibility = build_evidence_visibility(
                call_id=f"{state['run_id']}:business_fallback",
                evidence_catalog=[],
            )
            return NodeOutput((fallback, "business_repair_exhausted", visibility))
        repair_catalog, visibility = build_evidence_visibility(
            call_id=(
                f"{state['run_id']}:business_repair:{state.get('repair_count', 0) + 1}"
            ),
            evidence_catalog=list(state.get("evidence_catalog", [])),
        )
        if not state["runtime_config"].business_repair_llm_enabled:
            # Sunset criterion fired (see config docs): the LLM repair call
            # is disabled; degrade straight to the deterministic template.
            fallback = fallback_candidate(context, state["intent"].replan_mode)
            _, visibility = build_evidence_visibility(
                call_id=f"{state['run_id']}:business_repair_disabled",
                evidence_catalog=[],
            )
            return NodeOutput((fallback, "business_repair_disabled", visibility))

        AGENT_REPAIR_PATH.inc(
            make_labels(path="llm_unknown" if unknown_codes else "llm")
        )
        raw = await self._provider.repair_business_rules(
            candidate=state["candidate_plan"],
            context=context,
            repair_instructions=validation.repair_instructions,
            message=state["request"].message,
            replan_mode=state["intent"].replan_mode,
            evidence_catalog=repair_catalog,
        )
        usage = extract_provider_usage(raw)
        self._budget.record_llm_call(usage.tokens_in, usage.tokens_out)
        try:
            response = ProviderPlanResponse.model_validate(raw)
        except ValidationError:
            fallback = fallback_candidate(context, state["intent"].replan_mode)
            _, fallback_visibility = build_evidence_visibility(
                call_id=f"{state['run_id']}:business_invalid_fallback",
                evidence_catalog=[],
            )
            return NodeOutput(
                (fallback, "business_repair_invalid", fallback_visibility),
                build_provider_telemetry(
                    usage,
                    state["runtime_config"].prompt_versions["business_repair"],
                    visibility,
                ),
            )
        state["plan_provenance"] = "llm_repair"
        if response.violation_category is not None:
            state["violation_category"] = response.violation_category
        return NodeOutput(
            (response.candidate, None, visibility),
            build_provider_telemetry(
                usage,
                state["runtime_config"].prompt_versions["business_repair"],
                visibility,
            ),
        )

    async def _update_run(self, run_id: UUID, **values: object) -> None:
        async with self._session_factory() as session:
            async with session_transaction(session):
                run = await session.scalar(
                    select(AgentRun).where(AgentRun.id == run_id).with_for_update()
                )
                if run is None:
                    raise RuntimeError("Run not found")
                for key, value in values.items():
                    setattr(run, key, value)
                await session.flush()

    @staticmethod
    def _derive_provenance(state: PlanningState) -> str:
        if state.get("fallback_reason") is not None:
            return "fallback"
        return state.get("plan_provenance") or "model_pass"

    @staticmethod
    async def _validate_with_trace(
        candidate: PlanCandidate,
        context: PlanningContext,
        visibility: EvidenceVisibility,
        attempt: int,
    ) -> NodeOutput[ValidationReport]:
        report = validate_candidate(candidate, context, visibility)
        failed_checks = [check.code for check in report.checks if not check.passed]
        return NodeOutput(
            report,
            NodeTelemetry(
                trace_data={
                    "check_count": len(report.checks),
                    "failed_checks": failed_checks,
                    "attempt": attempt,
                }
            ),
        )

    @staticmethod
    async def _immediate[T](value: T, trace: dict[str, object]) -> NodeOutput[T]:
        return NodeOutput(value, NodeTelemetry(trace_data=trace))

class GraphFactory:
    """Build the fixed Stage 4 graph with an explicit Tool registry."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        provider: PlanningProvider,
        tool_registry: ToolRegistry | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        context_reader: PlanningContextPort | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider
        self._tool_registry = tool_registry or ToolRegistry()
        self._embedding_provider = embedding_provider or MockEmbeddingProvider()
        if context_reader is None:
            raise ValueError("context_reader is required")
        self._context_reader = context_reader

    def build(
        self,
        *,
        node_runner: NodeRunner,
        finalizer: PlanningResultPort,
        budget: BudgetGuard,
    ) -> FixedPlanningGraph:
        return FixedPlanningGraph(
            session_factory=self._session_factory,
            provider=self._provider,
            node_runner=node_runner,
            finalizer=finalizer,
            budget=budget,
            tool_registry=self._tool_registry,
            embedding_provider=self._embedding_provider,
            context_reader=self._context_reader,
        )


async def load_profile(
    session_factory: async_sessionmaker[AsyncSession], user_id: UUID
) -> ProfileContext | None:
    async with session_factory() as session:
        async with session_transaction(session):
            profile = await session.get(UserProfile, user_id)
            if profile is None:
                return None
            return ProfileContext.model_validate(profile, from_attributes=True)
