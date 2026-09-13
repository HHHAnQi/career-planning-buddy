"""Bounded Resume optimization graph using persisted nodes and domain tools."""

import json
from collections.abc import Awaitable, Callable, Mapping
from hashlib import sha256
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.errors import StructuredOutputError
from app.agent.finalizer import AgentRunFinalizer
from app.agent.node_runner import NodeOutput, NodeRunner, NodeTelemetry
from app.agent.resume_optimization_context import build_resume_optimization_context
from app.agent.resume_optimization_nodes import validate_faithfulness
from app.harness.budget import BudgetGuard
from app.harness.checkpoints import CheckpointStore
from app.prompts.resume_optimization import (
    RESUME_OPTIMIZATION_PROMPT_VERSION,
    resume_prompt_token_estimate,
)
from app.providers.embedding import EmbeddingProvider
from app.providers.resume_optimization import ResumeOptimizationProvider
from app.schemas.agent_runs import ProviderUsage
from app.schemas.enums import GoalType, RunIntent
from app.schemas.resumes import (
    ResumeClaimToolEvidence,
    ResumeOptimizationCandidate,
    ResumeOptimizationInputSnapshot,
    ResumeOptimizationState,
    ResumeToolEvidenceBundle,
)
from app.tools.contracts import (
    InterviewEvidenceRetrieveItem,
    ResumeGapItem,
    ToolContext,
)
from app.tools.registry import ToolRegistry


class ResumeOptimizationGraph:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        provider: ResumeOptimizationProvider,
        tool_registry: ToolRegistry,
        node_runner: NodeRunner,
        finalizer: AgentRunFinalizer,
        budget: BudgetGuard,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self._sessions = session_factory
        self._provider = provider
        self._tools = tool_registry
        self._nodes = node_runner
        self._finalizer = finalizer
        self._budget = budget
        self._embedding = embedding_provider
        self._checkpoints = CheckpointStore(session_factory)

    async def execute(self, state: ResumeOptimizationState) -> None:
        frozen_context = await self._checkpoints.load(
            state["run_id"], "resume_context_builder"
        )
        snapshot = await self._nodes.run(
            state["run_id"],
            "resume_context_builder",
            (
                lambda: self._restored_context(frozen_context)
                if frozen_context is not None
                else self._context(state)
            ),
            attempt=int(state.get("attempt_count", 1)),
        )
        state["input_snapshot"] = snapshot
        if frozen_context is None:
            await self._save_checkpoint(state, "resume_context_builder", snapshot)
        frozen_tools = await self._checkpoints.load(
            state["run_id"], "resume_domain_tools"
        )
        if frozen_tools is None:
            tool_evidence = await self._nodes.run_with_step(
                state["run_id"],
                "resume_domain_tools",
                lambda step_id: self._domain_tools(state, snapshot, step_id),
                attempt=int(state.get("attempt_count", 1)),
            )
            await self._save_checkpoint(state, "resume_domain_tools", tool_evidence)
        else:
            tool_evidence = await self._nodes.run(
                state["run_id"],
                "resume_domain_tools",
                lambda: self._restored_tool_evidence(frozen_tools),
                attempt=int(state.get("attempt_count", 1)),
            )
        state["tool_evidence"] = tool_evidence
        candidate = await self._nodes.run(
            state["run_id"],
            "resume_rewrite_generator",
            lambda: self._generate(state, snapshot, tool_evidence),
            attempt=int(state.get("attempt_count", 1)),
        )
        state["candidate"] = candidate
        validated = await self._nodes.run(
            state["run_id"],
            "resume_faithfulness_validator",
            lambda: self._validate_with_repair(
                state, candidate, snapshot, tool_evidence
            ),
            attempt=int(state.get("attempt_count", 1)),
        )
        persist_step = await self._nodes.start_step(
            state["run_id"], "resume_candidate_persist"
        )
        await self._finalizer.finalize_resume_optimization(
            run_id=state["run_id"],
            candidate=validated,
            snapshot=snapshot,
            persist_step_id=persist_step.id,
        )

    @staticmethod
    async def _restored_context(
        checkpoint: dict[str, object],
    ) -> NodeOutput[ResumeOptimizationInputSnapshot]:
        return NodeOutput(
            ResumeOptimizationInputSnapshot.model_validate(checkpoint),
            NodeTelemetry(trace_data={"checkpoint_reused": True}),
        )

    @staticmethod
    async def _restored_tool_evidence(
        checkpoint: dict[str, object],
    ) -> NodeOutput[ResumeToolEvidenceBundle]:
        return NodeOutput(
            ResumeToolEvidenceBundle.model_validate(checkpoint),
            NodeTelemetry(trace_data={"checkpoint_reused": True}),
        )

    async def _context(
        self, state: ResumeOptimizationState
    ) -> NodeOutput[ResumeOptimizationInputSnapshot]:
        snapshot = await build_resume_optimization_context(
            self._sessions,
            run_id=state["run_id"],
            user_id=state["user_id"],
            interview_session_id=state["interview_session_id"],
            resume_version_id=state["resume_version_id"],
            job_target_id=state["job_target_id"],
            embedding_provider=self._embedding,
            frozen_snapshot=(
                state["input_snapshot"].model_dump(mode="json")
                if state.get("input_snapshot") is not None
                else None
            ),
        )
        manifest = snapshot.context_manifest
        return NodeOutput(
            snapshot,
            NodeTelemetry(
                trace_data={
                    "algorithm_version": manifest.algorithm_version,
                    "candidate_count": len(manifest.candidates),
                    "selected_count": len(manifest.selected_evidence_refs),
                    "used_tokens": manifest.used_tokens,
                    "token_budget": manifest.token_budget,
                    "prompt_injection_filtered_count": manifest.prompt_injection_filtered_count,
                    "selection_manifest": manifest.model_dump(mode="json"),
                }
            ),
        )

    async def _domain_tools(
        self,
        state: ResumeOptimizationState,
        snapshot: ResumeOptimizationInputSnapshot,
        step_id: UUID,
    ) -> NodeOutput[ResumeToolEvidenceBundle]:
        context = ToolContext(
            run_id=state["run_id"], user_id=state["user_id"],
            goal_type=GoalType.AGENT_APP, intent=RunIntent.RESUME_OPTIMIZATION,
            remaining_deadline_ms=max(int(self._budget.remaining_seconds() * 1000), 1),
            replay_fixture_run_id=state.get("replay_of_run_id"),
            fixture_only=state.get("replay_tool_fixture_only", False),
        )
        calls: list[dict[str, object]] = []
        gap = await self._tools.execute(
            tool_name="resume_gap_analyze",
            arguments={
                "resume_version_id": str(snapshot.resume_version_id),
                "job_target_id": str(snapshot.job_target_id),
                "claim_ids": [item.claim_id for item in snapshot.claims],
            },
            context=context, step_id=step_id, round_number=1,
        )
        calls.append({"tool": "resume_gap_analyze", "success": gap.success, "reused": gap.reused})
        if not gap.success or gap.tool_call_id is None:
            raise StructuredOutputError("required Resume gap Tool failed")
        evidence_results = []
        if snapshot.interview_session_id is not None:
            batches = [
                snapshot.claims[index : index + 27]
                for index in range(0, len(snapshot.claims), 27)
            ]
            for index, batch in enumerate(batches):
                evidence = await self._tools.execute(
                tool_name="interview_evidence_retrieve",
                arguments={
                    "interview_session_id": str(snapshot.interview_session_id),
                    "claims": [
                        {"claim_id": item.claim_id, "claim_text": item.text}
                        for item in batch
                    ],
                    "limit_per_claim": 3,
                },
                context=context, step_id=step_id,
                round_number=1 if index == 0 else 2,
                )
                calls.append(
                    {
                        "tool": "interview_evidence_retrieve",
                        "success": evidence.success,
                        "reused": evidence.reused,
                    }
                )
                if not evidence.success or evidence.tool_call_id is None:
                    raise StructuredOutputError("required interview evidence Tool failed")
                evidence_results.append(evidence)
        if not all(bool(item["success"]) for item in calls):
            raise StructuredOutputError("required Resume domain tool failed")
        raw_gap_items = gap.result.data.get("items", [])
        gap_items = {
            item.claim_id: item
            for item in (
                ResumeGapItem.model_validate(value)
                for value in (raw_gap_items if isinstance(raw_gap_items, list) else [])
                if isinstance(value, dict)
            )
        }
        evidence_by_claim: dict[str, list[InterviewEvidenceRetrieveItem]] = {}
        evidence_call_by_claim: dict[str, UUID] = {}
        for result in evidence_results:
            assert result.tool_call_id is not None
            raw_items = result.result.data.get("items", [])
            for value in (raw_items if isinstance(raw_items, list) else []):
                if not isinstance(value, dict):
                    continue
                item = InterviewEvidenceRetrieveItem.model_validate(value)
                evidence_by_claim.setdefault(item.claim_id, []).append(item)
                evidence_call_by_claim[item.claim_id] = result.tool_call_id
        claim_evidence: list[ResumeClaimToolEvidence] = []
        unavailable: list[str] = []
        for claim in snapshot.claims:
            gap_item = gap_items.get(claim.claim_id)
            if gap_item is None:
                unavailable.append(claim.claim_id)
                gap_item = ResumeGapItem(
                    claim_id=claim.claim_id,
                    requirement_ids=[], coverage_score=0, gap="uncovered"
                )
            items = evidence_by_claim.get(claim.claim_id, [])
            call_ids = [gap.tool_call_id]
            if claim.claim_id in evidence_call_by_claim:
                call_ids.append(evidence_call_by_claim[claim.claim_id])
            claim_evidence.append(
                ResumeClaimToolEvidence(
                    claim_id=claim.claim_id,
                    gap=gap_item.gap,
                    coverage_score=gap_item.coverage_score,
                    requirement_ids=gap_item.requirement_ids,
                    evidence_turn_ids=[item.turn_id for item in items if item.relevance >= 0.08],
                    explicit_conflict_turn_ids=[
                        item.turn_id for item in items if item.explicit_conflict
                    ],
                    tool_call_ids=call_ids,
                )
            )
        raw_bundle = {
            "claims": [item.model_dump(mode="json") for item in claim_evidence],
            "unavailable_claim_ids": unavailable,
        }
        bundle_hash = sha256(
            json.dumps(raw_bundle, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        bundle = ResumeToolEvidenceBundle(**raw_bundle, bundle_hash=bundle_hash)
        return NodeOutput(
            bundle,
            NodeTelemetry(
                trace_data={
                    "tool_calls": calls,
                    "tool_evidence_bundle_hash": bundle.bundle_hash,
                    "covered_claim_count": len(bundle.claims) - len(unavailable),
                    "unavailable_claim_ids": unavailable,
                }
            ),
        )

    async def _generate(
        self,
        state: ResumeOptimizationState,
        snapshot: ResumeOptimizationInputSnapshot,
        tool_evidence: ResumeToolEvidenceBundle,
    ) -> NodeOutput[ResumeOptimizationCandidate]:
        prompt_tokens = resume_prompt_token_estimate(snapshot, tool_evidence)
        self._budget.validate_input_estimate(prompt_tokens)
        snapshot.context_manifest = snapshot.context_manifest.model_copy(
            update={"actual_prompt_tokens": prompt_tokens}
        )
        raw = await self._provider_fixture_or_call(
            state,
            "resume_provider_generate",
            lambda: self._provider.generate(snapshot, tool_evidence),
        )
        try:
            candidate, usage = self._parse(raw)
            candidate = self._restore_immutable_claim_fields(
                candidate, snapshot, tool_evidence
            )
            self._budget.record_llm_call(usage.tokens_in, usage.tokens_out)
        except (ValidationError, ValueError) as exc:
            validation_error = str(exc)
            self._budget.claim_format_repair()
            repaired = await self._provider_fixture_or_call(
                state,
                "resume_provider_format_repair",
                lambda: self._provider.repair(
                    snapshot, tool_evidence, raw, validation_error
                ),
            )
            candidate, usage = self._parse(repaired)
            candidate = self._restore_immutable_claim_fields(
                candidate, snapshot, tool_evidence
            )
            self._budget.record_llm_call(usage.tokens_in, usage.tokens_out)
        return NodeOutput(
            candidate,
            NodeTelemetry(
                trace_data={
                    "operation": "resume_optimization",
                    "actual_prompt_tokens": prompt_tokens,
                    "tool_evidence_bundle_hash": tool_evidence.bundle_hash,
                },
                tokens_in=usage.tokens_in, tokens_out=usage.tokens_out,
                cost_cny=usage.cost_cny, model_id=usage.model_id,
                prompt_version=RESUME_OPTIMIZATION_PROMPT_VERSION,
            ),
        )

    async def _validate_with_repair(
        self,
        state: ResumeOptimizationState,
        candidate: ResumeOptimizationCandidate,
        snapshot: ResumeOptimizationInputSnapshot,
        tool_evidence: ResumeToolEvidenceBundle,
    ) -> NodeOutput[ResumeOptimizationCandidate]:
        try:
            value = validate_faithfulness(candidate, snapshot, tool_evidence)
            return NodeOutput(value, NodeTelemetry(trace_data={"faithfulness_valid": True}))
        except StructuredOutputError as exc:
            validation_error = str(exc)
            if not self._budget.claim_business_repair():
                raise
            repaired = await self._provider_fixture_or_call(
                state,
                "resume_provider_business_repair",
                lambda: self._provider.repair(
                    snapshot, tool_evidence, candidate, validation_error
                ),
            )
            value, usage = self._parse(repaired)
            value = self._restore_immutable_claim_fields(value, snapshot, tool_evidence)
            self._budget.record_llm_call(usage.tokens_in, usage.tokens_out)
            validated = validate_faithfulness(value, snapshot, tool_evidence)
            return NodeOutput(
                validated,
                NodeTelemetry(
                    trace_data={"faithfulness_valid": True, "business_repair": True},
                    tokens_in=usage.tokens_in, tokens_out=usage.tokens_out,
                    cost_cny=usage.cost_cny, model_id=usage.model_id,
                    prompt_version=RESUME_OPTIMIZATION_PROMPT_VERSION,
                ),
            )

    @staticmethod
    def _parse(raw: Mapping[str, object]) -> tuple[ResumeOptimizationCandidate, ProviderUsage]:
        usage = ProviderUsage.model_validate(raw.get("usage"))
        payload = {key: value for key, value in raw.items() if key != "usage"}
        return ResumeOptimizationCandidate.model_validate(payload), usage

    @staticmethod
    def _restore_immutable_claim_fields(
        candidate: ResumeOptimizationCandidate,
        snapshot: ResumeOptimizationInputSnapshot,
        tool_evidence: ResumeToolEvidenceBundle,
    ) -> ResumeOptimizationCandidate:
        frozen = {item.claim_id: item for item in snapshot.claims}
        evidence = {item.claim_id: item for item in tool_evidence.claims}
        claims = []
        for finding in candidate.claims:
            source = frozen.get(finding.claim_id)
            if source is None:
                claims.append(finding)
                continue
            claims.append(
                finding.model_copy(
                    update={
                        "claim_text": source.text,
                        "source_start": source.source_start,
                        "source_end": source.source_end,
                        "source_hash": source.source_hash,
                        "consumed_tool_call_ids": (
                            evidence[finding.claim_id].tool_call_ids
                            if finding.claim_id in evidence
                            else finding.consumed_tool_call_ids
                        ),
                    }
                )
            )
        return candidate.model_copy(update={"claims": claims})

    async def _save_checkpoint(
        self,
        state: ResumeOptimizationState,
        node_name: str,
        value: ResumeOptimizationInputSnapshot | ResumeToolEvidenceBundle,
    ) -> None:
        await self._checkpoints.save(
            state["run_id"],
            int(state.get("attempt_count", 1)),
            node_name,
            value.model_dump(mode="json"),
        )

    async def _provider_fixture_or_call(
        self,
        state: ResumeOptimizationState,
        checkpoint_name: str,
        operation: Callable[[], Awaitable[Mapping[str, object]]],
    ) -> Mapping[str, object]:
        current = await self._checkpoints.load(state["run_id"], checkpoint_name)
        if current is not None:
            return current
        source_run_id = state.get("replay_of_run_id")
        if state.get("replay_provider_fixture_only") and source_run_id is not None:
            fixture = await self._checkpoints.load(source_run_id, checkpoint_name)
            if fixture is None:
                raise StructuredOutputError("REPLAY_PROVIDER_FIXTURE_MISSING")
            return fixture
        raw = await operation()
        value = dict(raw)
        await self._checkpoints.save(
            state["run_id"],
            int(state.get("attempt_count", 1)),
            checkpoint_name,
            value,
        )
        return value
