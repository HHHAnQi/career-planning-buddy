"""Build an evidence-bounded context projection for one Interview Run."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import session_transaction
from app.harness.snapshots import SnapshotService
from app.repositories.interviews import InterviewRepository
from app.repositories.resumes import ResumeRepository
from app.schemas.interviews import InterviewContext, InterviewReport, InterviewTurnResponse


async def build_interview_context(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    user_id: UUID,
    interview_id: UUID,
    current_turn_id: UUID | None,
) -> InterviewContext:
    async with session_factory() as db:
        async with session_transaction(db):
            interviews = InterviewRepository(db)
            materials = ResumeRepository(db)
            session = await interviews.get_session(interview_id, user_id)
            if session is None:
                raise RuntimeError("InterviewSession disappeared")
            resume = await materials.get_resume(
                session.resume_version_id, user_id, include_deleted=True
            )
            target = await materials.get_job_target(
                session.job_target_id, user_id, include_deleted=True
            )
            if resume is None or target is None:
                raise RuntimeError("frozen interview material disappeared")
            turns = await interviews.list_turns(session.id, user_id)
            current = next((item for item in turns if item.id == current_turn_id), None)
            completed = [item for item in turns if item.answer_status in {"submitted", "skipped"}]
            recent = completed[-2:]
            earlier = completed[:-2]
            raw_retest_keys = session.context_summary_json.get("retest_weakness_keys", [])
            retest_keys = raw_retest_keys if isinstance(raw_retest_keys, list) else []
            baseline_weaknesses = []
            if session.comparison_session_id is not None:
                baseline = await interviews.get_session(session.comparison_session_id, user_id)
                if baseline is None or baseline.report_json is None:
                    raise RuntimeError("retest baseline report disappeared")
                baseline_report = InterviewReport.model_validate(baseline.report_json)
                selected_keys = {str(item) for item in retest_keys if isinstance(item, str)}
                baseline_weaknesses = [
                    item
                    for item in baseline_report.weaknesses
                    if item.weakness_key in selected_keys
                ]
            context = InterviewContext(
                interview_id=session.id,
                interview_type=session.interview_type,
                question_limit=session.question_limit,
                followup_limit=session.followup_limit,
                asked_question_count=session.asked_question_count,
                followup_count=session.followup_count,
                resume_version_id=resume.id,
                resume_text=resume.source_text,
                resume_hash=resume.content_hash,
                job_target_id=target.id,
                job_title=target.title,
                company=target.company,
                jd_text=target.jd_text,
                jd_hash=target.content_hash,
                current_turn=(_turn_response(current) if current is not None else None),
                recent_turns=[_turn_response(item) for item in recent],
                earlier_turn_summary=[
                    {
                        "turn_id": str(item.id),
                        "topic_key": item.topic_key,
                        "answer_status": item.answer_status,
                        "covered_key_points": (item.analysis_json or {}).get(
                            "covered_key_points", []
                        ),
                        "missing_key_points": (item.analysis_json or {}).get(
                            "missing_key_points", []
                        ),
                    }
                    for item in earlier[-6:]
                ],
                asked_fingerprints=[item.question_fingerprint for item in turns][-8:],
                retest_weakness_keys=[
                    str(item) for item in retest_keys if isinstance(item, str)
                ][:3],
                baseline_weaknesses=baseline_weaknesses,
            )
            await SnapshotService.write_interview_input_once(
                db,
                run_id,
                {
                    "interview_id": str(session.id),
                    "session_version": session.version,
                    "resume_version_id": str(resume.id),
                    "resume_hash": resume.content_hash,
                    "job_target_id": str(target.id),
                    "job_target_hash": target.content_hash,
                    "turn_ids": [str(item.id) for item in turns],
                    "comparison_session_id": (
                        str(session.comparison_session_id)
                        if session.comparison_session_id is not None
                        else None
                    ),
                    "retest_weakness_keys": [
                        str(item) for item in retest_keys if isinstance(item, str)
                    ][:3],
                },
            )
            return context


def _turn_response(turn: object) -> InterviewTurnResponse:
    from app.models.interview import InterviewTurn
    from app.schemas.interviews import QuestionSource, TurnAnalysis

    if not isinstance(turn, InterviewTurn):
        raise TypeError("expected InterviewTurn")
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
        version=turn.version,
        answered_at=turn.answered_at,
        created_at=turn.created_at,
    )
