"""User-owned memory lifecycle and consent use cases."""

import hashlib
import json
from datetime import UTC, datetime
from http import HTTPStatus
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.errors import AgentError
from app.core.database import session_transaction
from app.core.exceptions import AppError
from app.models.evidence import Memory, MemoryCandidate
from app.providers.embedding import EmbeddingProvider
from app.repositories.memories import MemoryRepository
from app.schemas.memories import (
    MemoryCandidateDecisionResponse,
    MemoryCandidateListResponse,
    MemoryCandidateResponse,
    MemoryListResponse,
    MemoryPatchRequest,
    MemoryResponse,
)


class MemoryService:
    _DECISION_IDEMPOTENCY_CONSTRAINT = (
        "uq_memory_candidates_user_decision_idempotency"
    )

    def __init__(self, session: AsyncSession, embedding_provider: EmbeddingProvider) -> None:
        self._session = session
        self._embedding = embedding_provider
        self._repository = MemoryRepository(session)

    async def list_memories(
        self,
        *,
        user_id: UUID,
        memory_type: str | None,
        status: str,
        include_sensitive: bool,
        cursor: UUID | None,
        limit: int,
    ) -> MemoryListResponse:
        async with session_transaction(self._session):
            rows = await self._repository.list_memories(
                user_id=user_id,
                memory_type=memory_type,
                status=status,
                include_sensitive=include_sensitive,
                cursor=cursor,
                limit=limit + 1,
            )
            selected = rows[:limit]
            return MemoryListResponse(
                items=[self._memory_response(row) for row in selected],
                next_cursor=selected[-1].id if len(rows) > limit and selected else None,
            )

    async def patch_memory(
        self,
        *,
        memory_id: UUID,
        user_id: UUID,
        payload: MemoryPatchRequest,
    ) -> MemoryResponse:
        async with session_transaction(self._session):
            memory = await self._repository.get_memory(memory_id, user_id, for_update=True)
            if memory is None:
                raise self._not_found("Memory")
            if memory.version != payload.version:
                raise AppError(
                    code="STATE_MEMORY_VERSION_CONFLICT",
                    message="Memory version is stale",
                    status_code=HTTPStatus.CONFLICT,
                )
            if memory.status == payload.status:
                raise AppError(
                    code="STATE_MEMORY_TRANSITION_INVALID",
                    message="Memory status must transition between active and closed",
                    status_code=HTTPStatus.CONFLICT,
                )
            memory.status = payload.status
            memory.version += 1
            memory.updated_at = datetime.now(UTC)
            await self._session.flush()
            return self._memory_response(memory)

    async def delete_memory(self, *, memory_id: UUID, user_id: UUID) -> None:
        async with session_transaction(self._session):
            memory = await self._repository.get_memory(memory_id, user_id, for_update=True)
            if memory is None:
                raise self._not_found("Memory")
            await self._repository.delete_memory(memory)

    async def list_candidates(
        self,
        *,
        user_id: UUID,
        status: str,
        cursor: UUID | None,
        limit: int,
    ) -> MemoryCandidateListResponse:
        async with session_transaction(self._session):
            rows = await self._repository.list_candidates(
                user_id=user_id,
                status=status,
                cursor=cursor,
                limit=limit + 1,
            )
            for candidate in rows:
                self._repository.expire_if_needed(candidate)
            selected = [candidate for candidate in rows if candidate.status == status][:limit]
            await self._session.flush()
            return MemoryCandidateListResponse(
                items=[self._candidate_response(row) for row in selected],
                next_cursor=selected[-1].id if len(rows) > limit and selected else None,
            )

    async def confirm_candidate(
        self,
        *,
        candidate_id: UUID,
        user_id: UUID,
        idempotency_key: str,
    ) -> MemoryCandidateDecisionResponse:
        request_hash = self._decision_hash(candidate_id, "confirm")
        try:
            async with session_transaction(self._session):
                candidate = await self._candidate_for_idempotent_decision(
                    candidate_id=candidate_id,
                    user_id=user_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    action="confirm",
                )
                if candidate.status == "confirmed":
                    return await self._confirmed_response(candidate, user_id)
                if candidate.status != "pending":
                    raise self._candidate_conflict(candidate.status)
                vector = await self._best_effort_embedding(candidate.summary)
                weakness_key = candidate.content_json.get("weakness_key")
                memory = (
                    await self._repository.get_active_weakness(user_id, weakness_key)
                    if isinstance(weakness_key, str)
                    else None
                )
                if memory is None:
                    memory = await self._repository.create_memory(
                        user_id=user_id,
                        memory_type=candidate.memory_type,
                        summary=candidate.summary,
                        content_json=candidate.content_json,
                        sensitivity="sensitive",
                        embedding=vector,
                        source_run_id=candidate.proposed_by_run_id,
                    )
                else:
                    memory.summary = candidate.summary
                    memory.content_json = self._merge_weakness_content(
                        memory.content_json, candidate.content_json
                    )
                    memory.embedding = vector
                    memory.source_run_id = candidate.proposed_by_run_id
                    memory.version += 1
                    memory.updated_at = datetime.now(UTC)
                self._bind_decision(
                    candidate,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    action="confirm",
                )
                candidate.status = "confirmed"
                candidate.activated_memory_id = memory.id
                candidate.decided_at = datetime.now(UTC)
                await self._session.flush()
                return MemoryCandidateDecisionResponse(
                    candidate=self._candidate_response(candidate),
                    memory=self._memory_response(memory),
                )
        except IntegrityError as exc:
            if self._is_decision_idempotency_conflict(exc):
                raise self._idempotency_conflict() from exc
            raise

    async def reject_candidate(
        self,
        *,
        candidate_id: UUID,
        user_id: UUID,
        idempotency_key: str,
    ) -> MemoryCandidateDecisionResponse:
        request_hash = self._decision_hash(candidate_id, "reject")
        try:
            async with session_transaction(self._session):
                candidate = await self._candidate_for_idempotent_decision(
                    candidate_id=candidate_id,
                    user_id=user_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    action="reject",
                )
                if candidate.status == "rejected":
                    return MemoryCandidateDecisionResponse(
                        candidate=self._candidate_response(candidate)
                    )
                if candidate.status != "pending":
                    raise self._candidate_conflict(candidate.status)
                self._bind_decision(
                    candidate,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    action="reject",
                )
                candidate.status = "rejected"
                candidate.decided_at = datetime.now(UTC)
                await self._session.flush()
                return MemoryCandidateDecisionResponse(
                    candidate=self._candidate_response(candidate)
                )
        except IntegrityError as exc:
            if self._is_decision_idempotency_conflict(exc):
                raise self._idempotency_conflict() from exc
            raise

    async def _candidate_for_idempotent_decision(
        self,
        *,
        candidate_id: UUID,
        user_id: UUID,
        idempotency_key: str,
        request_hash: str,
        action: str,
    ) -> MemoryCandidate:
        existing = await self._repository.get_candidate_by_decision_key(
            user_id, idempotency_key
        )
        if existing is not None and existing.id != candidate_id:
            raise self._idempotency_conflict()
        candidate = await self._candidate_for_decision(candidate_id, user_id)
        if candidate.decision_idempotency_key is not None:
            if (
                candidate.decision_idempotency_key != idempotency_key
                or candidate.decision_request_hash != request_hash
                or candidate.decision_action != action
            ):
                raise self._idempotency_conflict()
        return candidate

    async def _confirmed_response(
        self, candidate: MemoryCandidate, user_id: UUID
    ) -> MemoryCandidateDecisionResponse:
        if candidate.activated_memory_id is None:
            raise AppError(
                code="STATE_MEMORY_ACTIVATION_MISSING",
                message="Confirmed candidate has no active Memory",
                status_code=HTTPStatus.CONFLICT,
            )
        memory = await self._repository.get_memory(candidate.activated_memory_id, user_id)
        if memory is None:
            raise AppError(
                code="STATE_MEMORY_ACTIVATION_MISSING",
                message="Confirmed candidate has no active Memory",
                status_code=HTTPStatus.CONFLICT,
            )
        return MemoryCandidateDecisionResponse(
            candidate=self._candidate_response(candidate),
            memory=self._memory_response(memory),
        )

    @staticmethod
    def _bind_decision(
        candidate: MemoryCandidate,
        *,
        idempotency_key: str,
        request_hash: str,
        action: str,
    ) -> None:
        candidate.decision_idempotency_key = idempotency_key
        candidate.decision_request_hash = request_hash
        candidate.decision_action = action

    @staticmethod
    def _decision_hash(candidate_id: UUID, action: str) -> str:
        payload = json.dumps(
            {"action": action, "candidate_id": str(candidate_id)},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _merge_weakness_content(
        current: dict[str, object], incoming: dict[str, object]
    ) -> dict[str, object]:
        merged = {**current, **incoming}
        for field in ("evidence_session_ids", "evidence_turn_ids"):
            current_values = current.get(field)
            incoming_values = incoming.get(field)
            values = [
                str(value)
                for value in [
                    *(current_values if isinstance(current_values, list) else []),
                    *(incoming_values if isinstance(incoming_values, list) else []),
                ]
            ]
            merged[field] = list(dict.fromkeys(values))[:20]
        merged_turns = merged.get("evidence_turn_ids")
        merged["observation_count"] = len(merged_turns) if isinstance(merged_turns, list) else 0
        merged["first_observed_at"] = current.get("first_observed_at") or incoming.get(
            "first_observed_at"
        )
        return merged

    @staticmethod
    def _idempotency_conflict() -> AppError:
        return AppError(
            code="STATE_IDEMPOTENCY_KEY_REUSED",
            message="Idempotency-Key was already used with another request",
            status_code=HTTPStatus.CONFLICT,
        )

    @classmethod
    def _is_decision_idempotency_conflict(cls, exc: IntegrityError) -> bool:
        current: BaseException | None = exc
        visited: set[int] = set()
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            constraint_name = getattr(current, "constraint_name", None)
            diag = getattr(current, "diag", None)
            if constraint_name == cls._DECISION_IDEMPOTENCY_CONSTRAINT or (
                diag is not None
                and getattr(diag, "constraint_name", None)
                == cls._DECISION_IDEMPOTENCY_CONSTRAINT
            ):
                return True
            if cls._DECISION_IDEMPOTENCY_CONSTRAINT in str(current):
                return True
            current = current.__cause__ or current.__context__
        return False

    async def _candidate_for_decision(self, candidate_id: UUID, user_id: UUID) -> MemoryCandidate:
        candidate = await self._repository.get_candidate(candidate_id, user_id, for_update=True)
        if candidate is None:
            raise self._not_found("Memory candidate")
        self._repository.expire_if_needed(candidate)
        return candidate

    async def _best_effort_embedding(self, text: str) -> list[float] | None:
        try:
            vectors = await self._embedding.embed([text])
        except AgentError:
            return None
        return vectors[0] if vectors else None

    @staticmethod
    def _memory_response(memory: Memory) -> MemoryResponse:
        return MemoryResponse(
            memory_id=memory.id,
            memory_type=memory.memory_type,
            summary=memory.summary,
            content=memory.content_json,
            sensitivity=memory.sensitivity,
            status=memory.status,
            source_run_id=memory.source_run_id,
            version=memory.version,
            last_used_at=memory.last_used_at,
            created_at=memory.created_at,
            updated_at=memory.updated_at,
        )

    @staticmethod
    def _candidate_response(candidate: MemoryCandidate) -> MemoryCandidateResponse:
        return MemoryCandidateResponse(
            candidate_id=candidate.id,
            memory_type=candidate.memory_type,
            summary=candidate.summary,
            content=candidate.content_json,
            sensitivity=candidate.sensitivity,
            status=candidate.status,
            proposed_by_run_id=candidate.proposed_by_run_id,
            activated_memory_id=candidate.activated_memory_id,
            expires_at=candidate.expires_at,
            created_at=candidate.created_at,
            decided_at=candidate.decided_at,
        )

    @staticmethod
    def _not_found(resource: str) -> AppError:
        return AppError(
            code="NOT_FOUND_MEMORY",
            message=f"{resource} was not found",
            status_code=HTTPStatus.NOT_FOUND,
        )

    @staticmethod
    def _candidate_conflict(status: str) -> AppError:
        return AppError(
            code="STATE_MEMORY_CANDIDATE_DECIDED",
            message=f"Memory candidate cannot be decided from status {status}",
            status_code=HTTPStatus.CONFLICT,
        )
