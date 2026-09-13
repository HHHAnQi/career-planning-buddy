"""Provider-neutral generation of user-confirmed Task adjustment proposals."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from app.core.config import Settings
from app.providers.llm_client import LLMClient
from app.providers.llm_contracts import LLMMessage, LLMRequest
from app.providers.llm_profiles import model_for_operation
from app.schemas.plans import TaskEditFields, TaskResponse


@dataclass(frozen=True, slots=True)
class TaskAdjustmentSuggestion:
    patch: TaskEditFields
    rationale: str
    generation_method: str
    model_id: str | None = None


class TaskAdjustmentProvider(Protocol):
    async def propose(
        self,
        *,
        task: TaskResponse,
        request_text: str,
        week_focus: str,
        success_signal: str,
        daily_budget_minutes: int,
    ) -> TaskAdjustmentSuggestion: ...


class RuleTaskAdjustmentProvider:
    async def propose(
        self,
        *,
        task: TaskResponse,
        request_text: str,
        week_focus: str,
        success_signal: str,
        daily_budget_minutes: int,
    ) -> TaskAdjustmentSuggestion:
        match = re.search(r"(?<!\d)(\d{1,3})\s*(?:分钟|min)", request_text, re.I)
        minutes = min(int(match.group(1)), daily_budget_minutes) if match else None
        requested_minutes = max(5, minutes) if minutes is not None else None
        starter = f"根据你的反馈，先完成“{task.starter_action}”中最小可验证的一步"
        return TaskAdjustmentSuggestion(
            patch=TaskEditFields(
                starter_action=starter[:240],
                estimated_minutes=requested_minutes,
                rationale=(
                    f"保持本周重点“{week_focus}”，同时根据用户反馈降低当天启动成本。"
                )[:500],
            ),
            rationale=f"调整仍以“{success_signal}”为本周成功标准。",
            generation_method="rule",
        )


class ModelTaskAdjustmentProvider:
    def __init__(self, settings: Settings, client: LLMClient) -> None:
        self._settings = settings
        self._client = client

    async def propose(
        self,
        *,
        task: TaskResponse,
        request_text: str,
        week_focus: str,
        success_signal: str,
        daily_budget_minutes: int,
    ) -> TaskAdjustmentSuggestion:
        response = await self._client.complete(
            LLMRequest(
                operation="task_adjustment",
                model=model_for_operation(self._settings, "task_adjustment"),
                structured_output="json_object",
                reasoning="off",
                temperature=0.1,
                max_output_tokens=700,
                messages=[
                    LLMMessage(
                        role="system",
                        content=(
                            "你是任务调整器。只调整尚未开始的一天任务，不改变周边界和本周重点。"
                            "返回 JSON：patch 与 rationale。patch 只允许 title、starter_action、"
                            "deliverable、rationale、estimated_minutes，至少修改一项。"
                        ),
                    ),
                    LLMMessage(
                        role="user",
                        content=json.dumps(
                            {
                                "task": task.model_dump(mode="json"),
                                "user_request": request_text,
                                "week_focus": week_focus,
                                "success_signal": success_signal,
                                "daily_budget_minutes": daily_budget_minutes,
                            },
                            ensure_ascii=False,
                        ),
                    ),
                ],
            )
        )
        if response.content is None:
            raise ValueError("task adjustment model returned no content")
        try:
            payload = json.loads(response.content)
            patch = TaskEditFields.model_validate(payload["patch"])
            rationale = str(payload["rationale"])
        except (json.JSONDecodeError, KeyError, TypeError, ValidationError) as exc:
            raise ValueError("task adjustment model returned an invalid proposal") from exc
        return TaskAdjustmentSuggestion(
            patch=patch,
            rationale=rationale[:500],
            generation_method="model",
            model_id=response.model_id,
        )


def build_task_adjustment_provider(
    settings: Settings, client: LLMClient | None
) -> TaskAdjustmentProvider:
    if client is None:
        return RuleTaskAdjustmentProvider()
    return ModelTaskAdjustmentProvider(settings, client)
