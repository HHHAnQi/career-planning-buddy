"""Provider-neutral interview generation and deterministic Mock adapter."""

import json
from collections.abc import Mapping
from typing import Protocol

from app.core.config import Settings
from app.prompts.interview import interview_messages, repair_messages
from app.providers.llm_client import LLMClient
from app.providers.llm_contracts import LLMMessage, LLMRequest, LLMResponse
from app.providers.llm_profiles import model_for_operation
from app.schemas.agent_runs import ProviderUsage
from app.schemas.interviews import InterviewContext


class InterviewProvider(Protocol):
    async def generate(
        self, *, operation: str, context: InterviewContext
    ) -> Mapping[str, object]: ...

    async def repair_format(
        self,
        *,
        operation: str,
        context: InterviewContext,
        raw_output: object,
        error: str,
    ) -> Mapping[str, object]: ...


class LLMInterviewProvider:
    def __init__(self, settings: Settings, client: LLMClient) -> None:
        self._client = client
        self._model = model_for_operation(settings, "planning")
        self._max_output_tokens = settings.agent_max_output_tokens_per_call

    async def generate(self, *, operation: str, context: InterviewContext) -> Mapping[str, object]:
        return await self._complete(operation, interview_messages(operation, context))

    async def repair_format(
        self,
        *,
        operation: str,
        context: InterviewContext,
        raw_output: object,
        error: str,
    ) -> Mapping[str, object]:
        return await self._complete(
            f"{operation}_format_repair",
            repair_messages(operation, context, raw_output, error),
        )

    async def _complete(self, operation: str, messages: list[LLMMessage]) -> Mapping[str, object]:
        response = await self._client.complete(
            LLMRequest(
                operation=operation,
                model=self._model,
                messages=messages,
                max_output_tokens=self._max_output_tokens,
                structured_output="json_object",
                temperature=0,
                reasoning="off",
            )
        )
        try:
            payload = json.loads(response.content or "")
        except json.JSONDecodeError:
            payload = {"_raw_text": (response.content or "")[:12000]}
        if not isinstance(payload, dict):
            payload = {"_raw_value": payload}
        return {**payload, "usage": _usage(response).model_dump(mode="json")}


class MockInterviewProvider:
    """Deterministic, evidence-grounded provider used by tests and local development."""

    model_id = "mock-interview-v1"

    async def generate(self, *, operation: str, context: InterviewContext) -> Mapping[str, object]:
        if operation == "question":
            payload = self._question(context)
        elif operation == "answer":
            payload = self._answer(context)
        elif operation == "report":
            payload = self._report(context)
        else:
            raise ValueError(f"unsupported interview operation: {operation}")
        return {**payload, "usage": self._usage()}

    async def repair_format(
        self,
        *,
        operation: str,
        context: InterviewContext,
        raw_output: object,
        error: str,
    ) -> Mapping[str, object]:
        del raw_output, error
        return await self.generate(operation=operation, context=context)

    @staticmethod
    def _question(context: InterviewContext) -> dict[str, object]:
        resume_excerpt = context.resume_text[:240].strip()
        jd_excerpt = context.jd_text[:240].strip()
        if context.retest_weakness_keys and context.baseline_weaknesses:
            weakness = context.baseline_weaknesses[0]
            return {
                "topic_key": weakness.weakness_key,
                "question_type": "technical",
                "question_text": (
                    f"复测题：请用一个新的具体案例说明你如何处理{weakness.topic}，"
                    "并给出关键行动、取舍和可验证结果。"
                ),
                "sources": [
                    {
                        "kind": "job_target",
                        "ref": str(context.job_target_id),
                        "excerpt": jd_excerpt,
                    }
                ],
            }
        if context.interview_type == "resume_deep_dive":
            question_text = (
                f"第 {context.asked_question_count + 1} 题：请选取简历中的一个项目，"
                "说明你的具体职责、关键取舍和可验证结果。"
            )
            return {
                "topic_key": f"resume-project-{context.asked_question_count + 1}",
                "question_type": "project",
                "question_text": question_text,
                "sources": [
                    {
                        "kind": "resume",
                        "ref": str(context.resume_version_id),
                        "excerpt": resume_excerpt,
                    }
                ],
            }
        question_text = (
            f"第 {context.asked_question_count + 1} 题：结合目标岗位“{context.job_title}”，"
            "请说明最相关的一段经历以及你如何满足核心要求。"
        )
        return {
            "topic_key": f"role-match-{context.asked_question_count + 1}",
            "question_type": "technical",
            "question_text": question_text,
            "sources": [
                {"kind": "job_target", "ref": str(context.job_target_id), "excerpt": jd_excerpt}
            ],
        }

    def _answer(self, context: InterviewContext) -> dict[str, object]:
        turn = context.current_turn
        if turn is None or turn.answer_text is None:
            raise ValueError("answer operation requires a submitted current turn")
        answer = turn.answer_text.strip()
        can_follow = (
            turn.parent_turn_id is None
            and context.followup_count < context.followup_limit
            and len(answer) < 160
        )
        reached_limit = context.asked_question_count >= context.question_limit
        next_action = "finish" if reached_limit else "followup" if can_follow else "next"
        next_question: dict[str, object] | None = None
        if next_action == "followup":
            excerpt = answer[:240]
            next_question = {
                "topic_key": turn.topic_key,
                "question_type": "followup",
                "question_text": f"你提到“{excerpt[:60]}”。请补充你本人做出的关键决策和结果证据。",
                "sources": [{"kind": "answer", "ref": str(turn.turn_id), "excerpt": excerpt}],
                "parent_turn_id": str(turn.turn_id),
            }
        elif next_action == "next":
            next_question = self._question(context)
        return {
            "analysis": {
                "covered_key_points": ["给出了与问题相关的回答"] if len(answer) >= 40 else [],
                "missing_key_points": ["补充本人行动、取舍和可验证结果"],
                "factual_findings": [
                    {
                        "claim": "当前材料不足以独立验证回答中的技术或结果主张",
                        "verdict": "insufficient_evidence",
                        "severity": "low",
                        "confidence": 0.5,
                        "rationale": "仅依据当前回答，未使用外部知识或隐含经历作判断。",
                        "evidence_refs": [str(turn.turn_id)],
                    }
                ],
                "answer_structure": {
                    "conclusion_first": len(answer) >= 80,
                    "logical_flow": "clear" if len(answer) >= 120 else "mixed",
                    "specificity": "specific" if len(answer) >= 160 else "mixed",
                    "concision": "balanced",
                },
                "improvement_actions": ["用结论—行动—结果三段式重述，并标明自己的贡献"],
                "suggested_outline": ["先给结论", "说明具体行动与取舍", "给出可验证结果"],
                "followup_reason": "需要把回答中的概括展开为可验证细节" if can_follow else None,
                "limitations": ["未接入外部技术知识验证，事实结论仅限提供的材料"],
            },
            "next_action": next_action,
            "next_question": next_question,
        }

    @staticmethod
    def _report(context: InterviewContext) -> dict[str, object]:
        answered = [turn for turn in context.recent_turns if turn.answer_status == "submitted"]
        evidence = [str(turn.turn_id) for turn in answered] or [
            str(context.current_turn.turn_id) if context.current_turn else str(context.interview_id)
        ]
        weakness_key = "answer-evidence-structure"
        summary = "本次报告只依据已保存的题目和回答，重点建议提升回答的证据密度与结构。"
        return {
            "overall_summary": summary,
            "strengths": ["完成了与目标岗位材料相关的结构化回答"],
            "weaknesses": [
                {
                    "weakness_key": weakness_key,
                    "topic": "回答证据与结构",
                    "dimension": "communication",
                    "severity": "medium",
                    "confidence": 0.72,
                    "evidence_turn_ids": evidence[:6],
                    "status": "observed",
                }
            ],
            "dimension_summary": [
                {"dimension": "communication", "observation": "需进一步明确本人行动与结果证据"}
            ],
            "recommended_training_actions": [
                {
                    "title": "重写一题项目回答",
                    "starter_action": "选择本场一题，先用一句话写结论",
                    "deliverable": "一份包含行动、取舍和结果证据的三段式回答",
                    "estimated_minutes": 20,
                    "source_weakness_keys": [weakness_key],
                }
            ],
            "limitations": ["报告不代表长期能力结论，也未使用外部技术事实库"],
        }

    def _usage(self) -> dict[str, object]:
        return ProviderUsage(
            model_id=self.model_id,
            tokens_in=120,
            tokens_out=100,
            latency_ms=1,
        ).model_dump(mode="json")


def _usage(response: LLMResponse) -> ProviderUsage:
    return ProviderUsage(
        model_id=response.model_id,
        provider=response.provider_id,
        request_id=response.request_id,
        raw_output_hash=response.raw_output_hash,
        tokens_in=response.usage.input_tokens,
        tokens_out=response.usage.output_tokens,
        latency_ms=response.latency_ms,
    )


def build_interview_provider(settings: Settings, client: LLMClient | None) -> InterviewProvider:
    if settings.llm_provider == "mock":
        return MockInterviewProvider()
    if client is None:
        raise RuntimeError("Interview Provider requires a shared LLM client")
    return LLMInterviewProvider(settings, client)
