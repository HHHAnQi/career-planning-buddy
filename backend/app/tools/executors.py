"""Read-only Stage 4 Tool handlers backed by repositories and Provider protocols."""

from decimal import Decimal
from hashlib import sha256
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.errors import AgentError, ProviderUnavailableError
from app.agent.resume_context_selection import lexical_similarity, requirement_matches
from app.core.database import session_transaction
from app.providers.embedding import EmbeddingProvider
from app.providers.rerank import RerankProvider
from app.providers.search import SearchProvider
from app.repositories.evidence import EvidenceRepository
from app.repositories.interviews import InterviewRepository
from app.repositories.resumes import ResumeRepository
from app.schemas.resumes import JobRequirement, ResumeClaim
from app.services.rag_documents import RagDocumentService
from app.services.resumes import stable_text_items
from app.tools.contracts import (
    DocumentSearchInput,
    DocumentSearchItem,
    DocumentSearchOutput,
    EvidenceItem,
    InterviewEvidenceRetrieveInput,
    InterviewEvidenceRetrieveItem,
    InterviewEvidenceRetrieveOutput,
    MemoryLookupInput,
    MemoryLookupItem,
    MemoryLookupOutput,
    RagEvidenceItem,
    RagRetrieveInput,
    RagRetrieveOutput,
    ResumeGapAnalyzeInput,
    ResumeGapAnalyzeOutput,
    ResumeGapItem,
    ToolContext,
    WebSearchInput,
    WebSearchItem,
    WebSearchOutput,
)
from app.tools.sanitization import sanitize_untrusted_text


class MemoryLookupHandler:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self._sessions = session_factory
        self._embedding = embedding_provider

    async def __call__(self, payload: BaseModel, context: ToolContext) -> BaseModel:
        request = MemoryLookupInput.model_validate(payload)
        vector: list[float] | None = None
        try:
            vectors = await self._embedding.embed([request.query])
            vector = vectors[0] if vectors else None
        except AgentError:
            vector = None
        async with self._sessions() as session:
            async with session_transaction(session):
                rows = await EvidenceRepository(session).memory_lookup(
                    user_id=context.user_id,
                    query=request.query,
                    vector=vector,
                    limit=request.limit,
                )
        items: list[MemoryLookupItem] = []
        evidence: list[EvidenceItem] = []
        remaining = 4000
        for memory, relevance in rows:
            content = _clean(memory.summary, min(remaining, 1000))
            if not content:
                continue
            remaining -= len(content)
            items.append(
                MemoryLookupItem(
                    memory_id=memory.id,
                    content=content,
                    memory_type=memory.memory_type,
                    relevance=relevance,
                    updated_at=memory.updated_at,
                )
            )
            evidence.append(
                EvidenceItem(
                    kind="memory",
                    id=memory.id,
                    title=memory.memory_type,
                    content=content,
                    reliability=0.9,
                )
            )
            if remaining <= 0:
                break
        return MemoryLookupOutput(items=items, evidence=evidence)


class RagRetrieveHandler:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedding_provider: EmbeddingProvider,
        min_similarity: float = 0.35,
    ) -> None:
        self._sessions = session_factory
        self._embedding = embedding_provider
        self._min_similarity = min_similarity

    async def __call__(self, payload: BaseModel, context: ToolContext) -> BaseModel:
        request = RagRetrieveInput.model_validate(payload)
        if request.goal_type != context.goal_type:
            raise ValueError("goal_type must match the current Run")
        vectors = await self._embedding.embed([request.query])
        if not vectors:
            raise ProviderUnavailableError("Embedding Provider returned no vector")
        async with self._sessions() as session:
            async with session_transaction(session):
                rows = await EvidenceRepository(session).rag_retrieve(
                    goal_type=context.goal_type.value,
                    vector=vectors[0],
                    limit=request.limit,
                    min_similarity=self._min_similarity,
                )
        items: list[RagEvidenceItem] = []
        evidence_items: list[EvidenceItem] = []
        for atom, score in rows:
            raw_reliability = atom.evidence_json.get("reliability", 0.7)
            reliability = (
                float(raw_reliability)
                if isinstance(raw_reliability, (int, float, Decimal))
                else 0.7
            )
            excerpt = atom.evidence_json.get("evidence_excerpt", "curated")
            evidence_text = _clean(str(excerpt), 300)
            content = _clean(atom.content, 1200)
            items.append(
                RagEvidenceItem(
                    atom_id=atom.id,
                    title=atom.title,
                    content=content,
                    evidence=evidence_text,
                    reliability=max(0.0, min(1.0, reliability)),
                    score=score,
                )
            )
            evidence_items.append(
                EvidenceItem(
                    kind="experience_atom",
                    id=atom.id,
                    title=atom.title,
                    content=content,
                    reliability=max(0.0, min(1.0, reliability)),
                )
            )
        return RagRetrieveOutput(items=items, evidence=evidence_items)


class WebSearchHandler:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        search_provider: SearchProvider,
    ) -> None:
        self._sessions = session_factory
        self._search = search_provider

    async def __call__(self, payload: BaseModel, context: ToolContext) -> BaseModel:
        request = WebSearchInput.model_validate(payload)
        raw_items = await self._search.search(
            query=request.query,
            limit=request.limit,
            freshness_days=request.freshness_days,
        )
        deduplicated: dict[str, object] = {}
        for item in raw_items:
            normalized_url = _normalize_url(item.url)
            if normalized_url and normalized_url not in deduplicated:
                deduplicated[normalized_url] = item
        items: list[WebSearchItem] = []
        evidence: list[EvidenceItem] = []
        async with self._sessions() as session:
            async with session_transaction(session):
                repository = EvidenceRepository(session)
                for normalized_url, raw in list(deduplicated.items())[: request.limit]:
                    from app.providers.search import SearchResultItem

                    source_item = SearchResultItem.model_validate(raw)
                    snippet = _clean(source_item.snippet, 1200)
                    title = _clean(source_item.title or "Untitled source", 300)
                    source = await repository.upsert_search_source(
                        run_id=context.run_id,
                        url=normalized_url,
                        url_hash=sha256(normalized_url.encode("utf-8")).hexdigest(),
                        content_hash=sha256(snippet.encode("utf-8")).hexdigest(),
                        title=title,
                        snippet=snippet,
                        source_type=source_item.source_type,
                        reliability=source_item.reliability,
                        provider=self._search.provider_name,
                        retrieved_at=source_item.retrieved_at,
                        provider_request_id=source_item.provider_request_id,
                        published_at=source_item.published_at,
                    )
                    items.append(
                        WebSearchItem(
                            source_id=source.id,
                            url=source.url,
                            title=source.title,
                            snippet=source.snippet,
                            source_type=source.source_type,
                            reliability=float(source.reliability),
                            retrieved_at=source.retrieved_at,
                        )
                    )
                    evidence.append(
                        EvidenceItem(
                            kind="search_source",
                            id=source.id,
                            title=source.title or "Search source",
                            content=source.snippet,
                            reliability=float(source.reliability),
                        )
                    )
        return WebSearchOutput(items=items, evidence=evidence)


class InterviewEvidenceRetrieveHandler:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def __call__(
        self, payload: BaseModel, context: ToolContext
    ) -> InterviewEvidenceRetrieveOutput:
        request = InterviewEvidenceRetrieveInput.model_validate(payload)
        async with self._sessions() as session:
            repository = InterviewRepository(session)
            interview = await repository.get_session(
                request.interview_session_id, context.user_id
            )
            if interview is None:
                raise ValueError("interview not found")
            turns = [
                turn for turn in await repository.list_turns(interview.id, context.user_id)
                if turn.answer_status == "submitted" and turn.answer_text
            ]
        items: list[InterviewEvidenceRetrieveItem] = []
        for query in request.claims:
            ranked: list[InterviewEvidenceRetrieveItem] = []
            for turn in turns:
                content = f"{turn.question_text} {turn.answer_text or ''}"
                relevance = lexical_similarity(query.claim_text, content)
                raw_findings = (
                    turn.analysis_json.get("factual_findings", [])
                    if isinstance(turn.analysis_json, dict)
                    else []
                )
                findings = raw_findings if isinstance(raw_findings, list) else []
                explicit_conflict = any(
                    isinstance(item, dict)
                    and item.get("verdict") == "incorrect"
                    and lexical_similarity(query.claim_text, str(item.get("claim", ""))) > 0
                    for item in findings
                )
                ranked.append(
                    InterviewEvidenceRetrieveItem(
                        claim_id=query.claim_id,
                        turn_id=turn.id,
                        question=turn.question_text[:300],
                        answer=(turn.answer_text or "")[:500],
                        relevance=relevance,
                        reliability=0.9 if turn.analysis_json else 0.72,
                        explicit_conflict=explicit_conflict,
                    )
                )
            items.extend(
                sorted(ranked, key=lambda item: (-item.relevance, str(item.turn_id)))[
                    : request.limit_per_claim
                ]
            )
        return InterviewEvidenceRetrieveOutput(
            items=items,
            evidence=[
                EvidenceItem(
                    kind="interview_turn", id=item.turn_id,
                    title=f"主张 {item.claim_id} 的面试回答 {item.turn_id}",
                    content=f"{item.question}\n{item.answer}",
                    reliability=item.reliability,
                )
                for item in {item.turn_id: item for item in items}.values()
            ],
        )


class ResumeGapAnalyzeHandler:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def __call__(self, payload: BaseModel, context: ToolContext) -> ResumeGapAnalyzeOutput:
        request = ResumeGapAnalyzeInput.model_validate(payload)
        async with self._sessions() as session:
            repository = ResumeRepository(session)
            resume = await repository.get_resume(request.resume_version_id, context.user_id)
            target = await repository.get_job_target(request.job_target_id, context.user_id)
            if resume is None or target is None:
                raise ValueError("resume or target not found")
        raw_claims = resume.structured_json.get(
            "claims", stable_text_items(resume.source_text, prefix="claim")
        )
        raw_requirements = target.requirements_json.get(
            "requirements", stable_text_items(target.jd_text, prefix="req")
        )
        claims = [
            ResumeClaim.model_validate(item)
            for item in (raw_claims if isinstance(raw_claims, list) else [])
            if isinstance(item, dict)
        ]
        requirements = [
            JobRequirement.model_validate(item)
            for item in (raw_requirements if isinstance(raw_requirements, list) else [])
            if isinstance(item, dict)
        ]
        selected = [claim for claim in claims if claim.claim_id in request.claim_ids]
        matches = requirement_matches(selected, requirements)
        items: list[ResumeGapItem] = []
        for claim in selected:
            linked = sorted(
                [item for item in matches if item.claim_id == claim.claim_id],
                key=lambda item: -item.final_score,
            )
            score = linked[0].final_score if linked else 0.0
            items.append(
                ResumeGapItem(
                    claim_id=claim.claim_id,
                    requirement_ids=[
                        item.requirement_id
                        for item in linked
                        if item.final_score >= 0.08
                    ],
                    coverage_score=score,
                    gap=(
                        "covered"
                        if score >= 0.45
                        else "partial"
                        if score >= 0.12
                        else "uncovered"
                    ),
                )
            )
        return ResumeGapAnalyzeOutput(items=items)


class DocumentSearchHandler:
    """Hybrid document retrieval over the current user's resume/JD chunks."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedding_provider: EmbeddingProvider,
        rerank_provider: RerankProvider,
        min_rerank_score: float,
    ) -> None:
        self._sessions = session_factory
        self._embedding = embedding_provider
        self._rerank = rerank_provider
        self._min_rerank_score = min_rerank_score

    async def __call__(self, payload: BaseModel, context: ToolContext) -> BaseModel:
        request = DocumentSearchInput.model_validate(payload)
        async with self._sessions() as session:
            service = RagDocumentService(
                session,
                embedding_provider=self._embedding,
                rerank_provider=self._rerank,
                min_rerank_score=self._min_rerank_score,
            )
            outcome = await service.search(
                user_id=context.user_id,
                query=request.query,
                limit=request.limit,
            )
        items: list[DocumentSearchItem] = []
        evidence: list[EvidenceItem] = []
        for result in outcome.results:
            content = _clean(result.chunk.content, 1200)
            items.append(
                DocumentSearchItem(
                    chunk_id=result.chunk.id,
                    doc_kind=result.chunk.doc_kind,
                    source_id=result.chunk.source_id,
                    title=_clean(result.chunk.title, 300),
                    content=content,
                    rerank_score=max(0.0, min(1.0, result.rerank_score)),
                    vector_rank=result.vector_rank,
                    lexical_rank=result.lexical_rank,
                )
            )
            evidence.append(
                EvidenceItem(
                    kind="rag_document_chunk",
                    id=result.chunk.id,
                    title=result.chunk.title,
                    content=content,
                    reliability=0.85,
                )
            )
        return DocumentSearchOutput(
            items=items,
            sufficient=outcome.sufficient,
            evidence=evidence,
        )


def _clean(value: str, limit: int) -> str:
    """Sanitize untrusted tool content before it enters the evidence catalog."""

    return sanitize_untrusted_text(value, limit)


def _normalize_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    filtered_query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=False)
        if not key.lower().startswith("utm_")
    ]
    hostname = parsed.hostname.lower() if parsed.hostname else ""
    port = parsed.port
    if port is not None and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        hostname = f"{hostname}:{port}"
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(
        (
            parsed.scheme.lower(),
            hostname,
            path,
            urlencode(sorted(filtered_query)),
            "",
        )
    )
