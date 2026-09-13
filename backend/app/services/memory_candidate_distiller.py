"""Deterministic, consent-first Review-to-MemoryCandidate distillation."""

import re
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import StrictModel

RULE_VERSION = "review_memory_v1"


class MemoryDistillationInput(StrictModel):
    user_id: UUID
    source_run_id: UUID | None
    review_id: UUID
    adjustment_request: str | None
    blockers: str | None
    free_text: str | None
    completed_count: int = Field(ge=0)
    abandoned_count: int = Field(ge=0)
    recent_blocker: str | None


class ProposedMemoryCandidate(StrictModel):
    memory_type: Literal["stable_preference", "execution_pattern"]
    summary: str = Field(min_length=1, max_length=120)
    content: dict[str, object]
    sensitivity: Literal["sensitive"] = "sensitive"


def distill_memory_candidates(
    value: MemoryDistillationInput,
) -> list[ProposedMemoryCandidate]:
    proposals: list[ProposedMemoryCandidate] = []
    if value.adjustment_request and _safe_text(value.adjustment_request):
        request = _normalize(value.adjustment_request)[:80]
        proposals.append(
            ProposedMemoryCandidate(
                memory_type="stable_preference",
                summary=f"用户希望后续计划：{request}"[:120],
                content={
                    "source_review_id": str(value.review_id),
                    "rule_version": RULE_VERSION,
                    "signal": "explicit_adjustment_request",
                    "preference": request,
                },
            )
        )
    blocker = _normalize(value.blockers) if value.blockers else ""
    repeated = bool(
        blocker
        and value.recent_blocker
        and blocker.casefold() == _normalize(value.recent_blocker).casefold()
    )
    if (repeated or value.abandoned_count >= 2) and (not blocker or _safe_text(blocker)):
        description = blocker[:80] if blocker else "任务范围或时间安排不匹配"
        proposals.append(
            ProposedMemoryCandidate(
                memory_type="execution_pattern",
                summary=(
                    f"用户反复受{description}阻碍"
                    if repeated
                    else f"用户因{description}多次放弃任务"
                )[:120],
                content={
                    "source_review_id": str(value.review_id),
                    "rule_version": RULE_VERSION,
                    "signal": "repeated_blocker" if repeated else "abandoned_tasks",
                    "blocker": description,
                },
            )
        )
    return proposals[:2]


def normalize_candidate_summary(summary: str) -> str:
    return _normalize(summary).casefold()


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _safe_text(value: str) -> bool:
    lowered = value.casefold()
    blocked_markers = (
        "身份证",
        "手机号",
        "电话",
        "微信",
        "邮箱",
        "住址",
        "病历",
        "诊断",
        "medication",
        "passport",
        "phone number",
        "email address",
    )
    return not any(marker in lowered for marker in blocked_markers)
