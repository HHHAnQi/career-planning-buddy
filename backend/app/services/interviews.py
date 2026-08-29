"""InterviewSession state machine and short AgentRun orchestration."""

import json
from builtins import list as builtin_list
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from http import HTTPStatus
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.executor import AgentRunExecutor
from app.core.config import Settings
from app.core.database import session_transaction
from app.core.exceptions import AppError
from app.harness.events import EventRecorder
from app.harness.snapshots import SnapshotService
from app.models.agent_run import AgentRun
from app.models.interview import InterviewSession, InterviewTurn
from app.repositories.agent_runs import AgentRunRepository
from app.repositories.interviews import InterviewRepository
from app.repositories.resumes import ResumeRepository
from app.schemas.interviews import (
    InterviewActiveRun,
    InterviewAnswerRequest,
    InterviewCreateRequest,
    InterviewReport,
    InterviewRunResponse,
    InterviewSessionResponse,
    InterviewTurnResponse,
)


class InterviewService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        executor: AgentRunExecutor,
    ) -> None:
        self._session = session
        self._settings = settings
        self._executor = executor
        self._interviews = InterviewRepository(session)
        self._runs = AgentRunRepository(session)
        self._materials = ResumeRepository(session)

    async def create(
        self,
        *,
        user_id: UUID,
        payload: InterviewCreateRequest,
        idempotency_key: str,
    ) -> InterviewRunResponse:
        request_hash = self._hash(payload.model_dump(mode="json"))
        created_run: AgentRun | None = None
        try:
            async with session_transaction(self._session):
                existing = await self._interviews.get_by_key(user_id, idempotency_key)
                if existing is not None:
                    self._validate_hash(existing.request_hash, request_hash)
                    run = await self._runs.get_for_interview(
                        session_id=existing.id,
                        user_id=user_id,
                        run_kind="interview_start",
                    )
                    if run is None:
                        raise self._state_error("initial interview Run is missing")
                    return self._run_response(existing.id, run)
                if await self._materials.get_resume(payload.resume_version_id, user_id) is None:
                    raise self._not_found("resume version")
                if await self._materials.get_job_target(payload.job_target_id, user_id) is None:
                    raise self._not_found("job target")
                await self._ensure_no_active_run(user_id)
                row = await self._interviews.create_session(
                    InterviewSession(
                        user_id=user_id,
                        resume_version_id=payload.resume_version_id,
                        job_target_id=payload.job_target_id,
                        interview_type=payload.interview_type,
                        question_limit=payload.question_limit,
                        followup_limit=payload.followup_limit,
                        idempotency_key=idempotency_key,
                        request_hash=request_hash,
                    )
                )
                created_run = await self._create_run(
                    user_id=user_id,
                    interview_id=row.id,
                    run_kind="interview_start",
                    request_text="Generate the first interview question",
                    key=f"interview-start-{row.id}",
                )
        except IntegrityError as exc:
            raise self._active_error() from exc
        if created_run is None:
            raise RuntimeError("interview Run creation did not converge")
        self._executor.submit(created_run.id)
        return self._run_response(created_run.interview_session_id, created_run)

    async def submit_answer(
        self,
        *,
        interview_id: UUID,
        user_id: UUID,
        payload: InterviewAnswerRequest,
        idempotency_key: str,
    ) -> InterviewRunResponse:
        request_hash = self._hash(payload.model_dump(mode="json"))
        created_run: AgentRun | None = None
        try:
            async with session_transaction(self._session):
                session = await self._require_session(interview_id, user_id, for_update=True)
                turn = await self._interviews.get_turn(payload.turn_id, user_id, for_update=True)
                if turn is None or turn.session_id != session.id:
                    raise self._not_found("interview turn")
                if turn.answer_idempotency_key == idempotency_key:
                    self._validate_hash(turn.answer_request_hash or "", request_hash)
                    if turn.analysis_run_id is None:
                        raise self._state_error("answer analysis Run is missing")
                    run = await self._runs.get_for_user(turn.analysis_run_id, user_id)
                    if run is None:
                        raise self._state_error("answer analysis Run is missing")
                    return self._run_response(session.id, run)
                if session.status != "active" or session.current_turn_id != turn.id:
                    raise self._state_error("the requested turn is not current")
                retrying = turn.answer_status == "submitted" and turn.analysis_status == "failed"
                if turn.answer_status != "pending" and not retrying:
                    raise self._state_error("the requested turn cannot accept an answer")
                if turn.version != payload.version:
                    raise self._version_error()
                if retrying and turn.answer_text != payload.answer_text.strip():
                    raise self._state_error("failed analysis may only retry the saved answer")
                await self._ensure_no_active_run(user_id)
                if not retrying:
                    turn.answer_text = payload.answer_text.strip()
                    turn.answer_status = "submitted"
                    turn.answered_at = datetime.now(UTC)
                turn.answer_idempotency_key = idempotency_key
                turn.answer_request_hash = request_hash
                turn.analysis_status = "running"
                turn.version += 1
                turn.updated_at = datetime.now(UTC)
                session.version += 1
                session.updated_at = datetime.now(UTC)
                created_run = await self._create_run(
                    user_id=user_id,
                    interview_id=session.id,
                    turn_id=turn.id,
                    run_kind="interview_answer",
                    request_text="Analyze the saved interview answer",
                    key=f"interview-answer-{turn.id}-{turn.version}",
                )
                turn.analysis_run_id = created_run.id
        except IntegrityError as exc:
            raise self._active_error() from exc
        if created_run is None:
            raise RuntimeError("answer Run creation did not converge")
        self._executor.submit(created_run.id)
        return self._run_response(interview_id, created_run)

    async def retry_start(
        self,
        *,
        interview_id: UUID,
        user_id: UUID,
        version: int,
        idempotency_key: str,
    ) -> InterviewRunResponse:
        created_run: AgentRun | None = None
        async with session_transaction(self._session):
            session = await self._require_session(interview_id, user_id, for_update=True)
            run_key = f"interview-start-retry-{idempotency_key}"[:64]
            existing = await self._runs.get_by_idempotency(user_id, run_key)
            if existing is not None:
                if existing.interview_session_id != session.id:
                    raise self._state_error("Idempotency-Key belongs to another interview")
                return self._run_response(session.id, existing)
            if session.status != "draft" or session.current_turn_id is not None:
                raise self._state_error("only an interview without a first question can retry")
            if session.version != version:
                raise self._version_error()
            latest = await self._runs.get_for_interview(
                session_id=session.id,
                user_id=user_id,
                run_kind="interview_start",
            )
            if latest is None or latest.status not in {"failed", "cancelled"}:
                raise self._state_error("the first question Run is not retryable")
            await self._ensure_no_active_run(user_id)
            session.version += 1
            session.updated_at = datetime.now(UTC)
            created_run = await self._create_run(
                user_id=user_id,
                interview_id=session.id,
                run_kind="interview_start",
                request_text="Retry generating the first interview question",
                key=run_key,
            )
        self._executor.submit(created_run.id)
        return self._run_response(interview_id, created_run)

    async def skip_turn(
        self,
        *,
        interview_id: UUID,
        user_id: UUID,
        turn_id: UUID,
        version: int,
        idempotency_key: str,
    ) -> InterviewRunResponse:
        created_run: AgentRun | None = None
        async with session_transaction(self._session):
            session = await self._require_session(interview_id, user_id, for_update=True)
            turn = await self._interviews.get_turn(turn_id, user_id, for_update=True)
            if turn is None or turn.session_id != session.id:
                raise self._not_found("interview turn")
            run_key = f"interview-skip-{idempotency_key}"[:64]
            existing = await self._runs.get_by_idempotency(user_id, run_key)
            if existing is not None:
                if (
                    existing.interview_session_id != session.id
                    or existing.interview_turn_id != turn.id
                ):
                    raise self._state_error("Idempotency-Key belongs to another action")
                return self._run_response(session.id, existing)
            if session.status != "active" or session.current_turn_id != turn.id:
                raise self._state_error("the requested turn is not current")
            retrying = turn.answer_status == "skipped"
            if turn.answer_status not in {"pending", "skipped"} or turn.version != version:
                raise self._version_error()
            await self._ensure_no_active_run(user_id)
            if not retrying:
                turn.answer_status = "skipped"
            turn.version += 1
            turn.updated_at = datetime.now(UTC)
            session.version += 1
            session.updated_at = datetime.now(UTC)
            created_run = await self._create_run(
                user_id=user_id,
                interview_id=session.id,
                turn_id=turn.id,
                run_kind="interview_start",
                request_text=(
                    "Retry the next question after a skipped turn"
                    if retrying
                    else "Generate the next question after a skipped turn"
                ),
                key=run_key,
            )
        self._executor.submit(created_run.id)
        return self._run_response(interview_id, created_run)

    async def finish(
        self,
        *,
        interview_id: UUID,
        user_id: UUID,
        version: int,
        idempotency_key: str,
        retry: bool = False,
    ) -> InterviewRunResponse:
        created_run: AgentRun | None = None
        async with session_transaction(self._session):
            session = await self._require_session(interview_id, user_id, for_update=True)
            run_key = f"interview-report-{idempotency_key}"[:64]
            existing = await self._runs.get_by_idempotency(user_id, run_key)
            if existing is not None:
                if existing.interview_session_id != session.id:
                    raise self._state_error("Idempotency-Key belongs to another interview")
                return self._run_response(session.id, existing)
            if retry:
                if session.report_status != "failed":
                    raise self._state_error("only a failed report can be retried")
            elif session.status != "active":
                raise self._state_error("only an active interview can be finished")
            if session.version != version:
                raise self._version_error()
            if await self._interviews.count_submitted_turns(session.id, user_id) < 1:
                raise self._state_error("at least one submitted answer is required")
            await self._ensure_no_active_run(user_id)
            if session.current_turn_id is not None:
                current = await self._interviews.get_turn(
                    session.current_turn_id, user_id, for_update=True
                )
                if current is not None and current.answer_status == "pending":
                    current.answer_status = "skipped"
                    current.version += 1
                    current.updated_at = datetime.now(UTC)
            session.current_turn_id = None
            session.status = "report_generating"
            session.report_status = "generating"
            session.version += 1
            session.updated_at = datetime.now(UTC)
            created_run = await self._create_run(
                user_id=user_id,
                interview_id=session.id,
                run_kind="interview_report",
                request_text="Generate the evidence-grounded interview report",
                key=run_key,
            )
            session.report_run_id = created_run.id
        self._executor.submit(created_run.id)
        return self._run_response(interview_id, created_run)

    async def get(self, interview_id: UUID, user_id: UUID) -> InterviewSessionResponse:
        async with session_transaction(self._session):
            session = await self._require_session(interview_id, user_id)
            turns = await self._interviews.list_turns(session.id, user_id)
            active_run = await self._runs.get_active_for_interview(session.id, user_id)
            return self.to_response(session, turns, active_run)

    async def list(self, user_id: UUID) -> list[InterviewSessionResponse]:
        async with session_transaction(self._session):
            rows = await self._interviews.list_sessions(user_id)
            result = []
            for row in rows:
                active_run = await self._runs.get_active_for_interview(row.id, user_id)
                result.append(
                    self.to_response(
                        row,
                        await self._interviews.list_turns(row.id, user_id),
                        active_run,
                    )
                )
            return result

    async def delete(self, interview_id: UUID, user_id: UUID) -> None:
        async with session_transaction(self._session):
            session = await self._require_session(interview_id, user_id, for_update=True)
            if await self._runs.get_active_for_interview(session.id, user_id) is not None:
                raise self._state_error("an active interview cannot be deleted")
            await self._interviews.delete_session(session)

    async def _create_run(
        self,
        *,
        user_id: UUID,
        interview_id: UUID,
        run_kind: str,
        request_text: str,
        key: str,
        turn_id: UUID | None = None,
    ) -> AgentRun:
        config = SnapshotService.build_interview_config(self._settings)
        run = await self._runs.create(
            AgentRun(
                user_id=user_id,
                run_kind=run_kind,
                interview_session_id=interview_id,
                interview_turn_id=turn_id,
                idempotency_key=key[:64],
                request_text=request_text,
                hint_intent=run_kind,
                resolved_intent=run_kind,
                status="pending",
                graph_version=config.graph_version,
                config_snapshot_json=config.model_dump(mode="json"),
                deadline_at=datetime.now(UTC) + timedelta(seconds=config.deadline_seconds),
            )
        )
        await EventRecorder(self._session).record(
            run.id,
            "run.created",
            {
                "status": "pending",
                "graph_version": run.graph_version,
                "run_kind": run_kind,
                "interview_id": str(interview_id),
            },
        )
        return run

    async def _ensure_no_active_run(self, user_id: UUID) -> None:
        if await self._runs.get_active_for_user(user_id) is not None:
            raise self._active_error()

    async def _require_session(
        self, interview_id: UUID, user_id: UUID, *, for_update: bool = False
    ) -> InterviewSession:
        row = await self._interviews.get_session(interview_id, user_id, for_update=for_update)
        if row is None:
            raise self._not_found("interview")
        return row

    @staticmethod
    def to_response(
        interview: InterviewSession,
        turns: Sequence[InterviewTurn],
        active_run: AgentRun | None = None,
    ) -> InterviewSessionResponse:
        return InterviewSessionResponse(
            interview_id=interview.id,
            resume_version_id=interview.resume_version_id,
            job_target_id=interview.job_target_id,
            interview_type=interview.interview_type,
            status=interview.status,
            question_limit=interview.question_limit,
            followup_limit=interview.followup_limit,
            asked_question_count=interview.asked_question_count,
            followup_count=interview.followup_count,
            current_turn_id=interview.current_turn_id,
            active_run=(
                InterviewActiveRun(
                    run_id=active_run.id,
                    run_kind=active_run.run_kind,
                    status=active_run.status,
                    events_url=f"/api/v1/agent-runs/{active_run.id}/events",
                )
                if active_run is not None
                else None
            ),
            turns=[InterviewService.turn_response(turn) for turn in turns],
            report_status=interview.report_status,
            report=(
                InterviewReport.model_validate(interview.report_json)
                if interview.report_json is not None
                else None
            ),
            comparison_session_id=interview.comparison_session_id,
            retest_weakness_keys=InterviewService._retest_keys(interview),
            version=interview.version,
            started_at=interview.started_at,
            completed_at=interview.completed_at,
            created_at=interview.created_at,
            updated_at=interview.updated_at,
        )

    @staticmethod
    def turn_response(turn: InterviewTurn) -> InterviewTurnResponse:
        from app.schemas.interviews import AudioAnalysis, QuestionSource, TurnAnalysis

        return InterviewTurnResponse(
            turn_id=turn.id,
            ordinal=turn.ordinal,
            parent_turn_id=turn.parent_turn_id,
            topic_key=turn.topic_key,
            question_type=turn.question_type,
            question_text=turn.question_text,
            question_sources=[
                QuestionSource.model_validate(item) for item in turn.question_sources_json
            ],
            answer_text=turn.answer_text,
            answer_status=turn.answer_status,
            analysis_status=turn.analysis_status,
            analysis=(
                TurnAnalysis.model_validate(turn.analysis_json)
                if turn.analysis_json is not None
                else None
            ),
            audio_analysis=(
                AudioAnalysis.model_validate(turn.audio_analysis_json)
                if turn.audio_analysis_json is not None
                else None
            ),
            version=turn.version,
            answered_at=turn.answered_at,
            created_at=turn.created_at,
        )

    @staticmethod
    def _retest_keys(interview: InterviewSession) -> builtin_list[str]:
        values = interview.context_summary_json.get("retest_weakness_keys", [])
        if not isinstance(values, list):
            return []
        return [str(item) for item in values if isinstance(item, str)][:3]

    @staticmethod
    def _run_response(interview_id: UUID | None, run: AgentRun) -> InterviewRunResponse:
        if interview_id is None:
            raise RuntimeError("Interview Run is missing its Session")
        return InterviewRunResponse(
            interview_id=interview_id,
            run_id=run.id,
            events_url=f"/api/v1/agent-runs/{run.id}/events",
        )

    @staticmethod
    def _hash(payload: object) -> str:
        return sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _validate_hash(actual: str, expected: str) -> None:
        if actual != expected:
            raise AppError(
                code="STATE_IDEMPOTENCY_KEY_REUSED",
                message="Idempotency-Key was already used with another request",
                status_code=HTTPStatus.CONFLICT,
            )

    @staticmethod
    def _not_found(name: str) -> AppError:
        return AppError(
            code="NOT_FOUND_INTERVIEW_RESOURCE",
            message=f"{name} was not found",
            status_code=HTTPStatus.NOT_FOUND,
        )

    @staticmethod
    def _state_error(message: str) -> AppError:
        return AppError(
            code="STATE_INTERVIEW_INVALID",
            message=message,
            status_code=HTTPStatus.CONFLICT,
        )

    @staticmethod
    def _version_error() -> AppError:
        return AppError(
            code="STATE_VERSION_CONFLICT",
            message="the interview resource changed; refresh and retry",
            status_code=HTTPStatus.CONFLICT,
        )

    @staticmethod
    def _active_error() -> AppError:
        return AppError(
            code="STATE_RUN_ALREADY_ACTIVE",
            message="another Agent Run is already active",
            status_code=HTTPStatus.CONFLICT,
        )
