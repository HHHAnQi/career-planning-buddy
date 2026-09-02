"""PostgreSQL integration test for the complete Batch 1 Mock interview chain."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.agent.executor import AgentRunExecutor
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.models.agent_run import AgentEvent, AgentRun
from app.schemas.interviews import InterviewAnswerRequest, InterviewCreateRequest
from app.schemas.resumes import JobTargetCreateRequest, ResumeVersionCreateRequest
from app.services.interview_persistence import InterviewPersistenceService
from app.services.interviews import InterviewService
from app.services.resumes import ResumeService
from tests.test_agent_runtime import ManualExecutor, create_user, runtime_factory


async def test_mock_runtime_persists_first_question_analysis_and_unique_terminals(
    db_connection: AsyncConnection, db_session: AsyncSession
) -> None:
    user_id = await create_user(db_session)
    materials = ResumeService(db_session)
    resume = await materials.create_resume(
        user_id=user_id,
        payload=ResumeVersionCreateRequest(
            label="后端简历",
            source_text="负责 FastAPI 服务、PostgreSQL 数据建模，并完成自动化测试和上线交付。",
        ),
        idempotency_key="runtime-resume",
    )
    target = await materials.create_job_target(
        user_id=user_id,
        payload=JobTargetCreateRequest(
            title="后端工程师",
            jd_text="负责 Python 后端服务开发，要求 FastAPI、PostgreSQL 和自动化测试经验。",
        ),
        idempotency_key="runtime-target",
    )
    scheduler = ManualExecutor()
    service = InterviewService(db_session, get_settings(), scheduler)
    started = await service.create(
        user_id=user_id,
        payload=InterviewCreateRequest(
            resume_version_id=resume.resume_version_id,
            job_target_id=target.job_target_id,
            interview_type="role_focused",
        ),
        idempotency_key="runtime-session",
    )
    factory = runtime_factory(db_connection)
    await AgentRunExecutor(factory).execute(started.run_id)
    session = await service.get(started.interview_id, user_id)
    assert session.status == "active"
    assert session.current_turn_id is not None
    turn = session.turns[0]
    answered = await service.submit_answer(
        interview_id=session.interview_id,
        user_id=user_id,
        payload=InterviewAnswerRequest(
            answer_text="我负责接口设计和数据建模，使用自动化测试覆盖关键路径，并按计划完成上线交付。",
            turn_id=turn.turn_id,
            version=turn.version,
        ),
        idempotency_key="runtime-answer",
    )
    replayed = await service.submit_answer(
        interview_id=session.interview_id,
        user_id=user_id,
        payload=InterviewAnswerRequest(
            answer_text="我负责接口设计和数据建模，使用自动化测试覆盖关键路径，并按计划完成上线交付。",
            turn_id=turn.turn_id,
            version=turn.version,
        ),
        idempotency_key="runtime-answer",
    )
    assert replayed.run_id == answered.run_id
    await AgentRunExecutor(factory).execute(answered.run_id)
    restored = await service.get(session.interview_id, user_id)
    assert restored.turns[0].answer_text is not None
    assert restored.turns[0].analysis_status == "ready"
    assert restored.current_turn_id is not None
    report_run = await service.finish(
        interview_id=session.interview_id,
        user_id=user_id,
        version=restored.version,
        idempotency_key="runtime-report",
    )
    await AgentRunExecutor(factory).execute(report_run.run_id)
    completed = await service.get(session.interview_id, user_id)
    assert completed.status == "completed"
    assert completed.report_status == "ready"
    assert completed.report is not None
    with pytest.raises(AppError, match="only a failed report can be retried"):
        await service.finish(
            interview_id=session.interview_id,
            user_id=user_id,
            version=completed.version,
            idempotency_key="runtime-ready-report-must-not-regenerate",
            retry=True,
        )
    for run_id in (started.run_id, answered.run_id, report_run.run_id):
        terminal_events = list(
            await db_session.scalars(
                select(AgentEvent).where(
                    AgentEvent.run_id == run_id,
                    AgentEvent.event_type.in_(
                        ("run.completed", "run.degraded", "run.failed", "run.cancelled")
                    ),
                )
            )
        )
        assert len(terminal_events) == 1


async def test_failed_answer_keeps_original_text_and_failed_report_can_retry(
    db_connection: AsyncConnection, db_session: AsyncSession
) -> None:
    user_id = await create_user(db_session)
    materials = ResumeService(db_session)
    resume = await materials.create_resume(
        user_id=user_id,
        payload=ResumeVersionCreateRequest(
            label="失败恢复简历",
            source_text="负责 FastAPI 服务和 PostgreSQL 查询优化，并维护自动化测试与故障恢复流程。",
        ),
        idempotency_key="failure-resume",
    )
    target = await materials.create_job_target(
        user_id=user_id,
        payload=JobTargetCreateRequest(
            title="Python 后端工程师",
            jd_text="要求 FastAPI、PostgreSQL 查询优化、自动化测试、可观测性和故障恢复经验。",
        ),
        idempotency_key="failure-target",
    )
    scheduler = ManualExecutor()
    service = InterviewService(db_session, get_settings(), scheduler)
    started = await service.create(
        user_id=user_id,
        payload=InterviewCreateRequest(
            resume_version_id=resume.resume_version_id,
            job_target_id=target.job_target_id,
            interview_type="role_focused",
        ),
        idempotency_key="failure-session",
    )
    factory = runtime_factory(db_connection)
    await AgentRunExecutor(factory).execute(started.run_id)
    current = await service.get(started.interview_id, user_id)
    turn = current.turns[0]
    answer_text = "我先用 EXPLAIN ANALYZE 定位慢查询，再用同一数据集压测验证。"
    answer_run = await service.submit_answer(
        interview_id=current.interview_id,
        user_id=user_id,
        payload=InterviewAnswerRequest(
            answer_text=answer_text,
            turn_id=turn.turn_id,
            version=turn.version,
        ),
        idempotency_key="failure-answer",
    )
    run = await db_session.get(AgentRun, answer_run.run_id)
    assert run is not None
    run.status = "failed"
    run.error_code = "TEST_PROVIDER_FAILED"
    run.error_message = "simulated provider failure"
    await InterviewPersistenceService(db_session).mark_unsuccessful(run)
    await db_session.commit()
    failed_answer = await service.get(current.interview_id, user_id)
    assert failed_answer.turns[0].answer_text == answer_text
    assert failed_answer.turns[0].analysis_status == "failed"

    retried = await service.submit_answer(
        interview_id=current.interview_id,
        user_id=user_id,
        payload=InterviewAnswerRequest(
            answer_text=answer_text,
            turn_id=turn.turn_id,
            version=failed_answer.turns[0].version,
        ),
        idempotency_key="failure-answer-retry",
    )
    await AgentRunExecutor(factory).execute(retried.run_id)
    ready_answer = await service.get(current.interview_id, user_id)
    assert ready_answer.turns[0].analysis_status == "ready"

    report_run = await service.finish(
        interview_id=current.interview_id,
        user_id=user_id,
        version=ready_answer.version,
        idempotency_key="failure-report",
    )
    report_agent_run = await db_session.get(AgentRun, report_run.run_id)
    assert report_agent_run is not None
    report_agent_run.status = "failed"
    report_agent_run.error_code = "TEST_PROVIDER_FAILED"
    report_agent_run.error_message = "simulated provider failure"
    await InterviewPersistenceService(db_session).mark_unsuccessful(report_agent_run)
    await db_session.commit()
    failed_report = await service.get(current.interview_id, user_id)
    assert failed_report.status == "active"
    assert failed_report.report_status == "failed"
    assert failed_report.turns[0].answer_text == answer_text

    retry_report = await service.finish(
        interview_id=current.interview_id,
        user_id=user_id,
        version=failed_report.version,
        idempotency_key="failure-report-retry",
        retry=True,
    )
    assert retry_report.run_id != report_run.run_id


async def test_failed_first_question_can_retry_without_recreating_session(
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    materials = ResumeService(db_session)
    resume = await materials.create_resume(
        user_id=user_id,
        payload=ResumeVersionCreateRequest(
            label="首题失败恢复简历",
            source_text="负责 FastAPI 服务、PostgreSQL 查询优化、自动化测试和故障恢复。",
        ),
        idempotency_key="start-retry-resume",
    )
    target = await materials.create_job_target(
        user_id=user_id,
        payload=JobTargetCreateRequest(
            title="Python 后端工程师",
            jd_text="要求 FastAPI、PostgreSQL、自动化测试、可观测性和故障恢复经验。",
        ),
        idempotency_key="start-retry-target",
    )
    scheduler = ManualExecutor()
    service = InterviewService(db_session, get_settings(), scheduler)
    started = await service.create(
        user_id=user_id,
        payload=InterviewCreateRequest(
            resume_version_id=resume.resume_version_id,
            job_target_id=target.job_target_id,
            interview_type="role_focused",
        ),
        idempotency_key="start-retry-session",
    )
    run = await db_session.get(AgentRun, started.run_id)
    assert run is not None
    run.status = "failed"
    run.error_code = "PROVIDER_UNAVAILABLE"
    run.error_message = "simulated provider failure"
    await InterviewPersistenceService(db_session).mark_unsuccessful(run)
    await db_session.commit()
    failed = await service.get(started.interview_id, user_id)
    assert failed.status == "draft"
    assert failed.current_turn_id is None

    retried = await service.retry_start(
        interview_id=started.interview_id,
        user_id=user_id,
        version=failed.version,
        idempotency_key="start-retry-action",
    )
    replayed = await service.retry_start(
        interview_id=started.interview_id,
        user_id=user_id,
        version=failed.version,
        idempotency_key="start-retry-action",
    )
    assert retried.interview_id == started.interview_id
    assert replayed.run_id == retried.run_id
