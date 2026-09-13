"""Transactional persistence operations for interview Run results."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.errors import PersistTransactionError
from app.agent.interview_nodes import question_fingerprint
from app.harness.events import EventRecorder
from app.models.agent_run import AgentRun
from app.models.interview import InterviewSession, InterviewTurn
from app.schemas.interviews import (
    InterviewAnswerCandidate,
    InterviewComparison,
    InterviewQuestionCandidate,
    InterviewReport,
    InterviewWeakness,
    WeaknessComparison,
)


class InterviewPersistenceService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def persist_question(
        self,
        *,
        run: AgentRun,
        interview: InterviewSession,
        candidate: InterviewQuestionCandidate,
    ) -> InterviewTurn:
        now = datetime.now(UTC)
        turn = InterviewTurn(
            user_id=run.user_id,
            session_id=interview.id,
            ordinal=interview.asked_question_count + 1,
            parent_turn_id=candidate.parent_turn_id,
            topic_key=candidate.topic_key,
            question_type=candidate.question_type,
            question_text=candidate.question_text,
            question_sources_json=[item.model_dump(mode="json") for item in candidate.sources],
            question_fingerprint=question_fingerprint(candidate.question_text),
            question_run_id=run.id,
        )
        self._session.add(turn)
        await self._session.flush()
        interview.status = "active"
        interview.current_turn_id = turn.id
        interview.asked_question_count += 1
        if candidate.question_type == "followup":
            interview.followup_count += 1
        interview.started_at = interview.started_at or now
        interview.version += 1
        interview.updated_at = now
        return turn

    async def persist_answer(
        self,
        *,
        run: AgentRun,
        interview: InterviewSession,
        candidate: InterviewAnswerCandidate,
    ) -> tuple[InterviewTurn, InterviewTurn | None]:
        turn = await self._session.scalar(
            select(InterviewTurn)
            .where(
                InterviewTurn.id == run.interview_turn_id,
                InterviewTurn.user_id == run.user_id,
                InterviewTurn.session_id == interview.id,
            )
            .with_for_update()
        )
        if turn is None or turn.answer_status != "submitted":
            raise PersistTransactionError("submitted InterviewTurn is missing")
        turn.analysis_json = candidate.analysis.model_dump(mode="json")
        turn.analysis_status = "ready"
        turn.version += 1
        turn.updated_at = datetime.now(UTC)
        next_turn = None
        if candidate.next_question is not None:
            next_turn = await self.persist_question(
                run=run,
                interview=interview,
                candidate=candidate.next_question,
            )
        else:
            interview.current_turn_id = None
            interview.version += 1
            interview.updated_at = datetime.now(UTC)
        return turn, next_turn

    async def persist_report(
        self,
        *,
        run: AgentRun,
        interview: InterviewSession,
        report: InterviewReport,
    ) -> None:
        valid_turn_ids = set(
            await self._session.scalars(
                select(InterviewTurn.id).where(
                    InterviewTurn.session_id == interview.id,
                    InterviewTurn.user_id == run.user_id,
                )
            )
        )
        if any(
            turn_id not in valid_turn_ids
            for weakness in report.weaknesses
            for turn_id in weakness.evidence_turn_ids
        ):
            raise PersistTransactionError("report references an invalid InterviewTurn")
        if interview.comparison_session_id is not None:
            baseline = await self._session.scalar(
                select(InterviewSession).where(
                    InterviewSession.id == interview.comparison_session_id,
                    InterviewSession.user_id == run.user_id,
                )
            )
            if baseline is None or baseline.report_json is None:
                raise PersistTransactionError("retest baseline report is unavailable")
            baseline_report = InterviewReport.model_validate(baseline.report_json)
            raw_selected = interview.context_summary_json.get("retest_weakness_keys", [])
            selected = raw_selected if isinstance(raw_selected, list) else []
            report = report.model_copy(
                update={
                    "comparison": self._comparison(
                        baseline_id=baseline.id,
                        current_id=interview.id,
                        baseline=baseline_report,
                        current=report,
                        selected_keys={str(item) for item in selected if isinstance(item, str)},
                    )
                }
            )
        now = datetime.now(UTC)
        interview.report_json = report.model_dump(mode="json")
        interview.report_version = (interview.report_version or 0) + 1
        interview.report_status = "ready"
        interview.status = "completed"
        interview.current_turn_id = None
        interview.completed_at = now
        interview.version += 1
        interview.updated_at = now

    @staticmethod
    def _comparison(
        *,
        baseline_id: UUID,
        current_id: UUID,
        baseline: InterviewReport,
        current: InterviewReport,
        selected_keys: set[str],
    ) -> InterviewComparison:
        severity_rank = {"low": 1, "medium": 2, "high": 3}
        current_by_key = {item.weakness_key: item for item in current.weaknesses}
        current_by_dimension = {(item.topic, item.dimension): item for item in current.weaknesses}
        items: list[WeaknessComparison] = []
        for old in baseline.weaknesses:
            if selected_keys and old.weakness_key not in selected_keys:
                continue
            new: InterviewWeakness | None = current_by_key.get(old.weakness_key)
            if new is None:
                new = current_by_dimension.get((old.topic, old.dimension))
            comparable = (
                new is not None
                and new.topic == old.topic
                and new.dimension == old.dimension
                and bool(new.evidence_turn_ids)
            )
            if not comparable:
                status = "insufficient_comparable_evidence"
            else:
                assert new is not None
                delta = severity_rank[new.severity] - severity_rank[old.severity]
                status = "improved" if delta < 0 else "regressed" if delta > 0 else "unchanged"
            items.append(
                WeaknessComparison(
                    weakness_key=old.weakness_key,
                    topic=old.topic,
                    dimension=old.dimension,
                    status=status,
                    baseline_severity=old.severity,
                    current_severity=new.severity if comparable and new is not None else None,
                    baseline_evidence_turn_ids=old.evidence_turn_ids,
                    current_evidence_turn_ids=(new.evidence_turn_ids if comparable and new else []),
                )
            )
        if not items:
            raise PersistTransactionError("retest has no selected baseline weakness")
        return InterviewComparison(
            baseline_session_id=baseline_id,
            current_session_id=current_id,
            items=items,
        )

    async def create_auto_report_run(
        self, *, source_run: AgentRun, interview: InterviewSession
    ) -> AgentRun:
        deadline_value = source_run.config_snapshot_json.get("deadline_seconds", 45)
        deadline_seconds = deadline_value if isinstance(deadline_value, int) else 45
        report_run = AgentRun(
            user_id=source_run.user_id,
            run_kind="interview_report",
            interview_session_id=interview.id,
            idempotency_key=f"interview-auto-report-{interview.id}"[:64],
            request_text="Generate the evidence-grounded interview report",
            hint_intent="interview_report",
            resolved_intent="interview_report",
            status="pending",
            graph_version=source_run.graph_version,
            config_snapshot_json=source_run.config_snapshot_json,
            deadline_at=datetime.now(UTC) + timedelta(seconds=deadline_seconds),
        )
        self._session.add(report_run)
        await self._session.flush()
        interview.status = "report_generating"
        interview.report_status = "generating"
        interview.report_run_id = report_run.id
        interview.version += 1
        interview.updated_at = datetime.now(UTC)
        await EventRecorder(self._session).record(
            report_run.id,
            "run.created",
            {
                "status": "pending",
                "graph_version": report_run.graph_version,
                "run_kind": "interview_report",
                "interview_id": str(interview.id),
            },
        )
        return report_run

    async def mark_unsuccessful(self, run: AgentRun) -> None:
        if not run.run_kind.startswith("interview_") or run.interview_session_id is None:
            return
        interview = await self._session.scalar(
            select(InterviewSession)
            .where(InterviewSession.id == run.interview_session_id)
            .with_for_update()
        )
        if interview is None:
            return
        now = datetime.now(UTC)
        if run.run_kind == "interview_answer" and run.interview_turn_id is not None:
            turn = await self._session.scalar(
                select(InterviewTurn)
                .where(InterviewTurn.id == run.interview_turn_id)
                .with_for_update()
            )
            if turn is not None:
                turn.analysis_status = "failed"
                turn.version += 1
                turn.updated_at = now
        elif run.run_kind == "interview_report":
            interview.report_status = "failed"
            interview.status = "active"
        interview.version += 1
        interview.updated_at = now
