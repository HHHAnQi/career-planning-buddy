"""Source-backed candidate distillation and developer review use cases."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import session_transaction
from app.models.evidence import ExperienceAtom, ExperienceAtomCandidate
from app.providers.embedding import EmbeddingProvider
from app.providers.evidence_distillation import EvidenceDistillationProvider
from app.repositories.evidence import EvidenceRepository
from app.schemas.experience_atoms import DistilledExperienceAtoms

_PRIVATE_MARKERS = ("身份证", "手机号", "微信", "邮箱", "住址", "passport", "phone number")


class ExperienceAtomService:
    def __init__(
        self,
        session: AsyncSession,
        embedding_provider: EmbeddingProvider,
        distillation_provider: EvidenceDistillationProvider,
    ) -> None:
        self._session = session
        self._embedding = embedding_provider
        self._distillation = distillation_provider
        self._repo = EvidenceRepository(session)

    async def distill_run(self, *, run_id: UUID, goal_type: str) -> list[ExperienceAtomCandidate]:
        async with session_transaction(self._session):
            sources = await self._repo.search_sources_for_run(run_id)
            if not sources:
                return []
            raw = await self._distillation.distill(goal_type=goal_type, sources=sources)
            try:
                result = DistilledExperienceAtoms.model_validate(raw)
            except ValidationError:
                repaired = await self._distillation.repair(raw_output=raw)
                result = DistilledExperienceAtoms.model_validate(repaired)
            source_map = {item.id: item for item in sources}
            saved: list[ExperienceAtomCandidate] = []
            for proposal in result.candidates[:3]:
                selected = [source_map[item] for item in proposal.source_ids if item in source_map]
                if len(selected) != len(proposal.source_ids):
                    continue
                if not any(proposal.evidence_excerpt in source.snippet for source in selected):
                    continue
                candidate_text = (proposal.content + proposal.evidence_excerpt).casefold()
                if any(marker in candidate_text for marker in _PRIVATE_MARKERS):
                    continue
                normalized = " ".join(proposal.content.split()).casefold()
                candidate = ExperienceAtomCandidate(
                    goal_type=goal_type,
                    title=proposal.title,
                    content=proposal.content,
                    source_ids=[str(item) for item in proposal.source_ids],
                    evidence_excerpt=proposal.evidence_excerpt,
                    confidence=Decimal(str(proposal.confidence)),
                    content_hash=sha256(normalized.encode()).hexdigest(),
                    status="pending",
                    proposed_by_run_id=run_id,
                    expires_at=datetime.now(UTC) + timedelta(days=30),
                )
                row = await self._repo.add_experience_candidate(candidate)
                if row is not None:
                    saved.append(row)
            return saved

    async def approve(self, candidate_id: UUID) -> ExperienceAtomCandidate:
        async with session_transaction(self._session):
            candidate = await self._required(candidate_id)
            if candidate.status == "approved" and candidate.approved_atom_id:
                return candidate
            if candidate.status != "pending" or candidate.expires_at <= datetime.now(UTC):
                raise ValueError("candidate is not pending")
            source_ids = [UUID(item) for item in candidate.source_ids]
            sources = await self._repo.sources_by_ids(
                run_id=candidate.proposed_by_run_id, source_ids=source_ids
            )
            if len(sources) != len(source_ids) or not any(
                candidate.evidence_excerpt in source.snippet for source in sources
            ):
                raise ValueError("candidate evidence is no longer valid")
            vectors = await self._embedding.embed([candidate.content])
            if not vectors:
                raise ValueError("embedding provider returned no vector")
            atom = ExperienceAtom(
                goal_type=candidate.goal_type,
                title=candidate.title,
                content=candidate.content,
                evidence_json={
                    "source_ids": candidate.source_ids,
                    "evidence_excerpt": candidate.evidence_excerpt,
                    "reliability": float(candidate.confidence),
                    "content_hash": candidate.content_hash,
                },
                embedding=vectors[0],
                is_active=True,
            )
            self._session.add(atom)
            await self._session.flush()
            candidate.status = "approved"
            candidate.approved_atom_id = atom.id
            candidate.decided_at = datetime.now(UTC)
            return candidate

    async def reject(self, candidate_id: UUID) -> ExperienceAtomCandidate:
        async with session_transaction(self._session):
            candidate = await self._required(candidate_id)
            if candidate.status == "rejected":
                return candidate
            if candidate.status != "pending":
                raise ValueError("candidate is not pending")
            candidate.status = "rejected"
            candidate.decided_at = datetime.now(UTC)
            return candidate

    async def _required(self, candidate_id: UUID) -> ExperienceAtomCandidate:
        candidate = await self._repo.candidate_for_update(candidate_id)
        if candidate is None:
            raise ValueError("candidate not found")
        return candidate
