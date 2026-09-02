"""Evidence-bounded resume/JD/interview context selection with an auditable manifest."""

import re
from datetime import UTC, datetime
from hashlib import sha256
from math import exp, log, sqrt

from app.agent.context_compression import estimate_text_tokens
from app.providers.embedding import EmbeddingProvider
from app.schemas.resumes import (
    JobRequirement,
    ResumeClaim,
    ResumeContextCandidate,
    ResumeContextManifest,
    ResumeRequirementMatch,
)

ALGORITHM_VERSION = "resume-context-rrf-mmr-v2"
_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I),
    re.compile(r"system\s*prompt", re.I),
    re.compile(r"忽略.{0,8}(指令|要求|规则)"),
    re.compile(r"你现在是.{0,20}(助手|系统)"),
)


def contains_prompt_injection(value: str) -> bool:
    return any(pattern.search(value) for pattern in _INJECTION_PATTERNS)


def tokens(value: str) -> set[str]:
    return {
        item.casefold()
        for item in re.findall(r"[A-Za-z0-9+#.]{2,}|[\u4e00-\u9fff]{2,}", value)
    }


def lexical_similarity(left: str, right: str) -> float:
    left_tokens, right_tokens = tokens(left), tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    return min(1.0, 2 * overlap / (len(left_tokens) + len(right_tokens)))


def semantic_proxy(left: str, right: str) -> float:
    """Legacy deterministic proxy retained only for old snapshots and unit baselines."""
    def grams(value: str) -> set[str]:
        normalized = re.sub(r"\s+", "", value.casefold())
        return {normalized[index : index + 2] for index in range(max(len(normalized) - 1, 0))}

    left_grams, right_grams = grams(left), grams(right)
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / len(left_grams | right_grams)


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    denominator = sqrt(sum(item * item for item in left)) * sqrt(
        sum(item * item for item in right)
    )
    if denominator == 0:
        return 0.0
    return max(0.0, min(1.0, sum(a * b for a, b in zip(left, right, strict=True)) / denominator))


async def hybrid_requirement_matches(
    claims: list[ResumeClaim],
    requirements: list[JobRequirement],
    embedding_provider: EmbeddingProvider,
) -> list[ResumeRequirementMatch]:
    """Fuse lexical and embedding rankings with RRF for every Resume claim."""
    if not claims or not requirements:
        return []
    texts = [item.text for item in claims] + [item.text for item in requirements]
    vectors = await embedding_provider.embed(texts)
    if len(vectors) != len(texts):
        raise ValueError("embedding provider returned an incomplete batch")
    claim_vectors = vectors[: len(claims)]
    requirement_vectors = vectors[len(claims) :]
    matches: list[ResumeRequirementMatch] = []
    for claim, claim_vector in zip(claims, claim_vectors, strict=True):
        scored = [
            (
                requirement,
                lexical_similarity(claim.text, requirement.text),
                _cosine(claim_vector, requirement_vector),
            )
            for requirement, requirement_vector in zip(
                requirements, requirement_vectors, strict=True
            )
        ]
        lexical_rank = {
            item[0].requirement_id: rank
            for rank, item in enumerate(
                sorted(scored, key=lambda value: (-value[1], value[0].requirement_id)),
                start=1,
            )
        }
        semantic_rank = {
            item[0].requirement_id: rank
            for rank, item in enumerate(
                sorted(scored, key=lambda value: (-value[2], value[0].requirement_id)),
                start=1,
            )
        }
        ranked: list[ResumeRequirementMatch] = []
        for requirement, lexical, semantic in scored:
            rrf = 1 / (60 + lexical_rank[requirement.requirement_id]) + 1 / (
                60 + semantic_rank[requirement.requirement_id]
            )
            # Normalize the two-list RRF maximum to [0, 1].
            rank_score = min(1.0, rrf / (2 / 61))
            signal = 0.45 * lexical + 0.55 * semantic
            final = rank_score * signal
            ranked.append(
                ResumeRequirementMatch(
                    claim_id=claim.claim_id,
                    requirement_id=requirement.requirement_id,
                    lexical_score=round(lexical, 4),
                    semantic_score=round(semantic, 4),
                    final_score=round(final, 4),
                    rationale="关键词与向量排名经 RRF 融合",
                )
            )
        matches.extend(
            sorted(ranked, key=lambda item: (-item.final_score, item.requirement_id))[:5]
        )
    return matches


def requirement_matches(
    claims: list[ResumeClaim], requirements: list[JobRequirement]
) -> list[ResumeRequirementMatch]:
    matches: list[ResumeRequirementMatch] = []
    for claim in claims:
        ranked: list[ResumeRequirementMatch] = []
        for requirement in requirements:
            lexical = lexical_similarity(claim.text, requirement.text)
            semantic = semantic_proxy(claim.text, requirement.text)
            score = min(1.0, 0.45 * lexical + 0.55 * semantic)
            ranked.append(
                ResumeRequirementMatch(
                    claim_id=claim.claim_id,
                    requirement_id=requirement.requirement_id,
                    lexical_score=round(lexical, 4),
                    semantic_score=round(semantic, 4),
                    final_score=round(score, 4),
                    rationale=(
                        "词项与语义片段均有交集" if lexical and semantic
                        else "存在语义片段交集" if semantic
                        else "没有可靠的内容交集"
                    ),
                )
            )
        matches.extend(
            sorted(ranked, key=lambda item: (-item.final_score, item.requirement_id))[:3]
        )
    return matches


def build_resume_context_manifest(
    *,
    claims: list[ResumeClaim],
    requirements: list[JobRequirement],
    evidence_turns: list[dict[str, object]],
    matches: list[ResumeRequirementMatch],
    token_budget: int = 3600,
    now: datetime | None = None,
    embedding_provider: str | None = None,
) -> ResumeContextManifest:
    selected_at = now or datetime.now(UTC)
    query = "\n".join([*(item.text for item in claims), *(item.text for item in requirements)])
    query_hash = sha256(query.encode()).hexdigest()
    max_match_by_claim = {
        claim.claim_id: max(
            (item.final_score for item in matches if item.claim_id == claim.claim_id),
            default=0.0,
        )
        for claim in claims
    }
    candidates: list[ResumeContextCandidate] = []
    filtered_count = 0

    def append(
        *, source_type: str, source_id: str, content: str, reliability: float,
        relevance: float, recency: float, version: int = 1,
    ) -> None:
        nonlocal filtered_count
        injection = contains_prompt_injection(content)
        if injection:
            filtered_count += 1
        stable = sha256(f"{source_type}:{source_id}".encode()).hexdigest()[:16]
        original_tokens = estimate_text_tokens(content)
        score = 0.55 * relevance + 0.30 * reliability + 0.15 * recency
        candidates.append(
            ResumeContextCandidate(
                context_item_id=f"ctx_{stable}", source_type=source_type,
                source_id=source_id, source_version=version,
                content_preview=content[:1000], relevance_score=round(relevance, 4),
                reliability_score=round(reliability, 4), recency_score=round(recency, 4),
                final_score=0.0 if injection else round(score, 4), selected=False,
                exclusion_reason="检测到潜在提示注入内容" if injection else None,
                original_token_count=original_tokens, final_token_count=0,
                compression_method="excluded", evidence_ref=f"{source_type}:{source_id}",
                rendered_content=None,
                content_hash=sha256(content.encode()).hexdigest(),
            )
        )

    for claim in claims:
        append(source_type="resume_claim", source_id=claim.claim_id, content=claim.text,
               reliability=0.95, relevance=max_match_by_claim[claim.claim_id], recency=1.0)
    for requirement in requirements:
        relevance = max(
            (
                item.final_score
                for item in matches
                if item.requirement_id == requirement.requirement_id
            ),
            default=0.0,
        )
        append(source_type="job_requirement", source_id=requirement.requirement_id,
               content=requirement.text, reliability=0.9, relevance=relevance, recency=1.0)
    for turn in evidence_turns:
        turn_id = str(turn["turn_id"])
        content = f"{turn.get('question_text', '')} {turn.get('answer_text', '')}".strip()
        relevance = max((lexical_similarity(claim.text, content) for claim in claims), default=0.0)
        answered_at = turn.get("answered_at")
        recency = 1.0
        if isinstance(answered_at, str):
            try:
                parsed = datetime.fromisoformat(answered_at.replace("Z", "+00:00"))
                age = max((selected_at - parsed).total_seconds() / 86400, 0)
                recency = exp(-log(2) * age / 90)
            except ValueError:
                pass
        append(source_type="interview_turn", source_id=turn_id, content=content,
               reliability=0.85 if turn.get("analysis_json") else 0.72,
               relevance=relevance, recency=recency)

    remaining_items = sorted(
        candidates,
        key=lambda item: (-item.final_score, item.source_type, item.source_id),
    )
    used = 0
    selected_types: set[str] = set()
    selected_items: list[ResumeContextCandidate] = []
    updated: dict[str, ResumeContextCandidate] = {}
    while remaining_items:
        def mmr_score(item: ResumeContextCandidate) -> tuple[float, str, str]:
            redundancy = max(
                (
                    lexical_similarity(item.content_preview, selected.content_preview)
                    for selected in selected_items
                ),
                default=0.0,
            )
            diversity = 0.08 if item.source_type not in selected_types else 0.0
            return (
                0.7 * item.final_score - 0.3 * redundancy + diversity,
                item.source_type,
                item.source_id,
            )

        item = max(remaining_items, key=mmr_score)
        remaining_items.remove(item)
        if item.exclusion_reason:
            updated[item.context_item_id] = item
            continue
        diversity_bonus = 0.08 if item.source_type not in selected_types else 0.0
        effective = min(1.0, item.final_score + diversity_bonus)
        min_score = 0.12 if item.source_type != "interview_turn" else 0.08
        if effective < min_score:
            updated[item.context_item_id] = item.model_copy(
                update={"exclusion_reason": "相关度低于选择阈值"}
            )
            continue
        remaining = token_budget - used
        if remaining <= 0:
            updated[item.context_item_id] = item.model_copy(
                update={"exclusion_reason": "Token 预算已用尽"}
            )
            continue
        final_tokens = min(item.original_token_count, remaining, 500)
        rendered = _truncate_to_tokens(item.content_preview, final_tokens)
        actual_tokens = estimate_text_tokens(rendered)
        if not rendered or actual_tokens <= 0:
            updated[item.context_item_id] = item.model_copy(
                update={"exclusion_reason": "Token 预算不足以保留有效内容"}
            )
            continue
        used += actual_tokens
        selected_types.add(item.source_type)
        selected_items.append(item)
        updated[item.context_item_id] = item.model_copy(
            update={
                "selected": True, "selection_reason": "综合相关度、可信度、时效与来源多样性入选",
                "exclusion_reason": None, "final_token_count": actual_tokens,
                "rendered_content": rendered,
                "compression_method": (
                    "truncate" if final_tokens < item.original_token_count else "none"
                ),
            }
        )
    final = [updated[item.context_item_id] for item in candidates]
    rendered_context = "\n".join(
        f"<{item.source_type} id=\"{item.source_id}\">{item.rendered_content}</{item.source_type}>"
        for item in final
        if item.selected and item.rendered_content
    )
    return ResumeContextManifest(
        query_hash=query_hash, algorithm_version=ALGORITHM_VERSION,
        token_budget=token_budget, used_tokens=used, candidates=final,
        selected_evidence_refs=[item.evidence_ref for item in final if item.selected],
        prompt_injection_filtered_count=filtered_count,
        rendered_context_hash=sha256(rendered_context.encode()).hexdigest(),
        embedding_provider=embedding_provider,
    )


def _truncate_to_tokens(value: str, budget: int) -> str:
    if estimate_text_tokens(value) <= budget:
        return value
    low, high = 0, len(value)
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_text_tokens(value[:middle]) <= budget:
            low = middle
        else:
            high = middle - 1
    return value[:low].rstrip()
