"""Deterministic candidate fallback and strict evidence-faithfulness validation."""

import re

from app.agent.errors import StructuredOutputError
from app.agent.resume_context_selection import contains_prompt_injection, lexical_similarity
from app.schemas.resumes import (
    ResumeClaimFinding,
    ResumeOptimizationCandidate,
    ResumeOptimizationInputSnapshot,
    ResumeToolEvidenceBundle,
)

_NUMERIC = re.compile(r"(?<![\w])\d+(?:\.\d+)?\s*(?:%|ms|s|秒|分钟|万|k|m)?", re.I)
_OWNERSHIP = ("主导", "负责", "独立", "owner", "led", "architected")


def deterministic_candidate(
    snapshot: ResumeOptimizationInputSnapshot,
    tool_evidence: ResumeToolEvidenceBundle,
) -> ResumeOptimizationCandidate:
    findings: list[ResumeClaimFinding] = []
    evidence_by_claim = {item.claim_id: item for item in tool_evidence.claims}
    for claim in snapshot.claims:
        evidence = evidence_by_claim.get(claim.claim_id)
        if evidence is None:
            raise StructuredOutputError("Tool evidence must cover every Resume claim")
        blocked = contains_prompt_injection(claim.text)
        if blocked:
            verdict, rationale = (
                "insufficient_evidence",
                "该主张包含潜在指令性内容，已从模型上下文隔离并等待人工核验。",
            )
        elif evidence.explicit_conflict_turn_ids:
            verdict, rationale = "unsupported", "面试分析中存在与该主张直接相关的冲突证据。"
        elif evidence.evidence_turn_ids and evidence.gap == "covered":
            verdict, rationale = "supported", "面试回答与目标要求共同提供了可回溯支持。"
        elif evidence.evidence_turn_ids:
            verdict, rationale = "partially_supported", "回答提供了相关行动，但细节或结果仍不完整。"
        else:
            verdict, rationale = (
                "insufficient_evidence",
                "当前没有可用的面试证据；该结果仅作为面试前差距诊断。",
            )
        rewrite = None
        if verdict != "supported":
            rewrite = _conservative_rewrite(claim.text, verdict)
        findings.append(
            ResumeClaimFinding(
                claim_id=claim.claim_id, claim_text=claim.text, verdict=verdict,
                rationale=rationale,
                requirement_ids=evidence.requirement_ids,
                evidence_turn_ids=evidence.evidence_turn_ids,
                suggested_rewrite=rewrite,
                consumed_tool_call_ids=evidence.tool_call_ids,
                source_start=claim.source_start,
                source_end=claim.source_end,
                source_hash=claim.source_hash,
            )
        )
    return ResumeOptimizationCandidate(
        claims=findings,
        limitations=[
            "结论仅依据冻结的简历、JD 与所选面试证据。",
            "证据不足不等同于主张错误；候选改写不会自动覆盖原简历。",
        ],
    )


def validate_faithfulness(
    candidate: ResumeOptimizationCandidate,
    snapshot: ResumeOptimizationInputSnapshot,
    tool_evidence: ResumeToolEvidenceBundle,
) -> ResumeOptimizationCandidate:
    claim_map = {item.claim_id: item for item in snapshot.claims}
    turn_map = {str(item["turn_id"]): item for item in snapshot.evidence_turns}
    requirement_ids = {item.requirement_id for item in snapshot.requirements}
    evidence_by_claim = {item.claim_id: item for item in tool_evidence.claims}
    if {item.claim_id for item in candidate.claims} != set(claim_map):
        raise StructuredOutputError("candidate must cover every frozen Resume claim exactly once")
    validated: list[ResumeClaimFinding] = []
    for finding in candidate.claims:
        original = claim_map[finding.claim_id].text
        consumed = evidence_by_claim.get(finding.claim_id)
        if consumed is None:
            raise StructuredOutputError("candidate has no Tool evidence for claim")
        if finding.claim_text != original:
            raise StructuredOutputError("candidate changed the frozen claim identity")
        claim_source = claim_map[finding.claim_id]
        if (
            finding.source_start != claim_source.source_start
            or finding.source_end != claim_source.source_end
            or finding.source_hash != claim_source.source_hash
        ):
            raise StructuredOutputError("candidate changed the frozen claim source span")
        if any(item not in requirement_ids for item in finding.requirement_ids):
            raise StructuredOutputError("candidate cited an unavailable requirement")
        if not set(finding.requirement_ids).issubset(consumed.requirement_ids):
            raise StructuredOutputError("candidate cited a requirement absent from Tool output")
        if set(finding.consumed_tool_call_ids) != set(consumed.tool_call_ids):
            raise StructuredOutputError("candidate did not declare the consumed Tool calls")
        evidence = [turn_map.get(str(item)) for item in finding.evidence_turn_ids]
        if any(item is None for item in evidence):
            raise StructuredOutputError("candidate cited an unavailable interview turn")
        if not set(finding.evidence_turn_ids).issubset(consumed.evidence_turn_ids):
            raise StructuredOutputError("candidate cited evidence absent from Tool output")
        if not evidence and finding.verdict != "insufficient_evidence":
            raise StructuredOutputError("claims without evidence must remain insufficient")
        if contains_prompt_injection(original) and finding.verdict != "insufficient_evidence":
            raise StructuredOutputError("blocked untrusted claim must remain insufficient")
        rewrite = finding.suggested_rewrite
        if rewrite:
            evidence_text = " ".join(str(item.get("answer_text", "")) for item in evidence if item)
            allowed_text = f"{original} {evidence_text}"
            invented_numbers = set(_NUMERIC.findall(rewrite)) - set(_NUMERIC.findall(allowed_text))
            if invented_numbers:
                raise StructuredOutputError("rewrite introduced unsupported numeric facts")
            for term in _OWNERSHIP:
                if (
                    term.casefold() in rewrite.casefold()
                    and term.casefold() not in allowed_text.casefold()
                ):
                    raise StructuredOutputError("rewrite escalated unsupported ownership")
            if finding.verdict == "insufficient_evidence" and rewrite != (
                _conservative_rewrite(original, finding.verdict)
            ):
                raise StructuredOutputError(
                    "insufficient evidence rewrite must preserve uncertainty"
                )
        validated.append(finding)
    return candidate.model_copy(update={"claims": validated})


def _conservative_rewrite(claim: str, verdict: str) -> str:
    if verdict == "unsupported":
        return f"参与相关工作：{claim}（仅保留可由现有材料验证的职责与结果）"
    return f"参与相关工作：{claim}（具体职责与结果待补充可验证证据）"


def _explicit_conflict(claim: str, analysis: object) -> bool:
    if not isinstance(analysis, dict):
        return False
    return any(
        isinstance(item, dict)
        and item.get("verdict") == "incorrect"
        and lexical_similarity(claim, str(item.get("claim", ""))) > 0
        for item in analysis.get("factual_findings", [])
    )
