"""Three short Interview Run paths sharing the existing NodeRunner and Finalizer."""

from collections.abc import Mapping
from decimal import Decimal
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.errors import StructuredOutputError
from app.agent.finalizer import AgentRunFinalizer
from app.agent.interview_context import build_interview_context
from app.agent.interview_nodes import validate_answer, validate_question, validate_report
from app.agent.node_runner import NodeOutput, NodeRunner, NodeTelemetry
from app.harness.budget import BudgetGuard
from app.providers.interview import InterviewProvider
from app.schemas.agent_runs import ProviderUsage
from app.schemas.interviews import (
    InterviewAnswerCandidate,
    InterviewContext,
    InterviewQuestionCandidate,
    InterviewReport,
    InterviewState,
    TurnAnalysis,
)


class InterviewGraph:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        provider: InterviewProvider,
        node_runner: NodeRunner,
        finalizer: AgentRunFinalizer,
        budget: BudgetGuard,
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider
        self._nodes = node_runner
        self._finalizer = finalizer
        self._budget = budget

    async def execute(self, state: InterviewState) -> None:
        context = await self._nodes.run(
            state["run_id"],
            "interview_context",
            lambda: self._context_output(state),
        )
        state["context"] = context
        operation, schema = self._operation(state["run_kind"])
        candidate = await self._nodes.run(
            state["run_id"],
            "interview_generate",
            lambda: self._generate(operation, schema, context),
        )
        validated = await self._nodes.run(
            state["run_id"],
            "interview_validate",
            lambda: self._validate_with_repair(
                state["run_kind"], operation, schema, candidate, context
            ),
        )
        persist_step = await self._nodes.start_step(state["run_id"], "interview_persist")
        await self._finalizer.finalize_interview(
            run_id=state["run_id"],
            candidate=cast(
                InterviewQuestionCandidate | InterviewAnswerCandidate | InterviewReport,
                validated,
            ),
            persist_step_id=persist_step.id,
        )

    async def _context_output(self, state: InterviewState) -> NodeOutput[InterviewContext]:
        context = await build_interview_context(
            self._session_factory,
            run_id=state["run_id"],
            user_id=state["user_id"],
            interview_id=state["interview_session_id"],
            current_turn_id=state.get("interview_turn_id"),
        )
        return NodeOutput(
            context,
            NodeTelemetry(
                trace_data={
                    "interview_id": str(context.interview_id),
                    "asked_question_count": context.asked_question_count,
                }
            ),
        )

    async def _generate(
        self,
        operation: str,
        schema: type[InterviewQuestionCandidate]
        | type[InterviewAnswerCandidate]
        | type[InterviewReport],
        context: InterviewContext,
    ) -> NodeOutput[object]:
        raw = await self._provider.generate(operation=operation, context=context)
        usages: list[ProviderUsage] = []
        try:
            value, usage = self._parse(schema, raw)
            usages.append(usage)
            self._record_usage(usage)
        except (ValidationError, ValueError) as exc:
            failed_usage = self._extract_usage(raw)
            if failed_usage is not None:
                usages.append(failed_usage)
                self._record_usage(failed_usage)
            self._budget.claim_format_repair()
            if not self._budget.can_reserve_llm_call():
                raise StructuredOutputError("interview output repair exceeds budget") from exc
            repaired = await self._provider.repair_format(
                operation=operation,
                context=context,
                raw_output=raw,
                error=str(exc),
            )
            value, repaired_usage = self._parse(schema, repaired)
            usages.append(repaired_usage)
            self._record_usage(repaired_usage)
        return NodeOutput(
            value,
            NodeTelemetry(
                trace_data={"operation": operation, "provider_calls": len(usages)},
                tokens_in=sum(item.tokens_in for item in usages),
                tokens_out=sum(item.tokens_out for item in usages),
                cost_cny=sum((item.cost_cny for item in usages), Decimal("0")),
                model_id=usages[-1].model_id,
                prompt_version="interview-v1",
            ),
        )

    @staticmethod
    async def _validate(
        run_kind: str, candidate: object, context: InterviewContext
    ) -> NodeOutput[object]:
        if run_kind == "interview_answer":
            answer = InterviewGraph._bind_answer_evidence(
                InterviewAnswerCandidate.model_validate(candidate), context
            )
            validated: object = validate_answer(answer, context)
        elif run_kind == "interview_report":
            validated = validate_report(InterviewReport.model_validate(candidate), context)
        else:
            validated = validate_question(
                InterviewQuestionCandidate.model_validate(candidate), context
            )
        return NodeOutput(validated, NodeTelemetry(trace_data={"evidence_valid": True}))

    @staticmethod
    def _bind_answer_evidence(
        answer: InterviewAnswerCandidate, context: InterviewContext
    ) -> InterviewAnswerCandidate:
        if (
            context.current_turn is not None
            and context.current_turn.parent_turn_id is not None
            and answer.next_question is not None
        ):
            answer = answer.model_copy(
                update={"next_action": "finish", "next_question": None}
            )
        if context.current_turn is None:
            return answer
        turn_ref = str(context.current_turn.turn_id)
        findings = [
            item.model_copy(update={"evidence_refs": [turn_ref]})
            for item in answer.analysis.factual_findings
        ]
        return answer.model_copy(
            update={
                "analysis": TurnAnalysis.model_validate(
                    {
                        **answer.analysis.model_dump(mode="python"),
                        "factual_findings": findings,
                    }
                )
            }
        )

    async def _validate_with_repair(
        self,
        run_kind: str,
        operation: str,
        schema: type[Any],
        candidate: object,
        context: InterviewContext,
    ) -> NodeOutput[object]:
        try:
            return await self._validate(run_kind, candidate, context)
        except StructuredOutputError as exc:
            if not self._budget.claim_business_repair():
                raise
            if not self._budget.can_reserve_llm_call():
                raise StructuredOutputError("interview business repair exceeds budget") from exc
            raw = await self._provider.repair_format(
                operation=operation,
                context=context,
                raw_output=candidate,
                error=f"business_rule_error={exc}",
            )
            repaired, usage = self._parse(schema, raw)
            self._record_usage(usage)
            output = await self._validate(run_kind, repaired, context)
            output.telemetry = NodeTelemetry(
                trace_data={"evidence_valid": True, "business_repair": True},
                tokens_in=usage.tokens_in,
                tokens_out=usage.tokens_out,
                cost_cny=usage.cost_cny,
                model_id=usage.model_id,
                prompt_version="interview-v1",
            )
            return output

    @staticmethod
    def _operation(run_kind: str) -> tuple[str, type[Any]]:
        if run_kind == "interview_answer":
            return "answer", InterviewAnswerCandidate
        if run_kind == "interview_report":
            return "report", InterviewReport
        return "question", InterviewQuestionCandidate

    @staticmethod
    def _parse(schema: type[Any], raw: Mapping[str, object]) -> tuple[object, ProviderUsage]:
        usage = ProviderUsage.model_validate(raw.get("usage"))
        payload = {key: value for key, value in raw.items() if key != "usage"}
        return schema.model_validate(payload), usage

    @staticmethod
    def _extract_usage(raw: Mapping[str, object]) -> ProviderUsage | None:
        try:
            return ProviderUsage.model_validate(raw.get("usage"))
        except ValidationError:
            return None

    def _record_usage(self, usage: ProviderUsage) -> None:
        self._budget.record_llm_call(usage.tokens_in, usage.tokens_out)
