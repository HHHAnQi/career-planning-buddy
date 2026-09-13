"""Batch 1 material and interview API identity/state contracts."""

from http import HTTPStatus

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun
from tests.test_profile_api import bearer, guest_login, profile_body


async def create_materials(client: AsyncClient, token: str, suffix: str) -> tuple[str, str]:
    resume = await client.post(
        "/api/v1/resume-versions",
        json={
            "label": "后端简历",
            "source_text": "负责 FastAPI 服务、PostgreSQL 数据建模，并完成自动化测试和上线交付。",
        },
        headers={**bearer(token), "Idempotency-Key": f"resume-{suffix}"},
    )
    target = await client.post(
        "/api/v1/job-targets",
        json={
            "title": "后端工程师",
            "company": "Example",
            "jd_text": "负责 Python 后端服务开发，要求 FastAPI、PostgreSQL 和自动化测试经验。",
        },
        headers={**bearer(token), "Idempotency-Key": f"target-{suffix}"},
    )
    assert resume.status_code == HTTPStatus.CREATED
    assert target.status_code == HTTPStatus.CREATED
    return resume.json()["resume_version_id"], target.json()["job_target_id"]


@pytest.mark.asyncio
async def test_materials_and_session_creation_are_idempotent(api_client: AsyncClient) -> None:
    token, _, _ = await guest_login(api_client)
    await api_client.put(
        "/api/v1/profile",
        json=profile_body(),
        headers={**bearer(token), "Idempotency-Key": "interview-profile"},
    )
    resume_id, target_id = await create_materials(api_client, token, "one")
    body = {
        "resume_version_id": resume_id,
        "job_target_id": target_id,
        "interview_type": "role_focused",
        "question_limit": 4,
        "followup_limit": 2,
    }
    first = await api_client.post(
        "/api/v1/interviews",
        json=body,
        headers={**bearer(token), "Idempotency-Key": "session-one"},
    )
    replay = await api_client.post(
        "/api/v1/interviews",
        json=body,
        headers={**bearer(token), "Idempotency-Key": "session-one"},
    )
    assert first.status_code == HTTPStatus.ACCEPTED
    assert replay.status_code == HTTPStatus.ACCEPTED
    assert replay.json() == first.json()
    restored = await api_client.get(
        f"/api/v1/interviews/{first.json()['interview_id']}", headers=bearer(token)
    )
    assert restored.json()["active_run"] == {
        "run_id": first.json()["run_id"],
        "run_kind": "interview_start",
        "status": "pending",
        "events_url": first.json()["events_url"],
    }


@pytest.mark.asyncio
async def test_material_soft_delete_hides_only_the_owners_inputs(
    api_client: AsyncClient,
) -> None:
    token_a, _, _ = await guest_login(api_client)
    token_b, _, _ = await guest_login(api_client)
    resume_id, target_id = await create_materials(api_client, token_a, "delete")

    forbidden = await api_client.delete(
        f"/api/v1/resume-versions/{resume_id}", headers=bearer(token_b)
    )
    removed_resume = await api_client.delete(
        f"/api/v1/resume-versions/{resume_id}", headers=bearer(token_a)
    )
    removed_target = await api_client.delete(
        f"/api/v1/job-targets/{target_id}", headers=bearer(token_a)
    )
    resumes = await api_client.get("/api/v1/resume-versions", headers=bearer(token_a))
    targets = await api_client.get("/api/v1/job-targets", headers=bearer(token_a))

    assert forbidden.status_code == HTTPStatus.NOT_FOUND
    assert removed_resume.status_code == HTTPStatus.NO_CONTENT
    assert removed_target.status_code == HTTPStatus.NO_CONTENT
    assert resumes.json()["items"] == []
    assert targets.json()["items"] == []


@pytest.mark.asyncio
async def test_completed_interview_can_be_deleted_by_its_owner(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    token, _, _ = await guest_login(api_client)
    resume_id, target_id = await create_materials(api_client, token, "session-delete")
    created = await api_client.post(
        "/api/v1/interviews",
        json={
            "resume_version_id": resume_id,
            "job_target_id": target_id,
            "interview_type": "role_focused",
        },
        headers={**bearer(token), "Idempotency-Key": "deletable-session"},
    )
    run = await db_session.scalar(
        select(AgentRun).where(AgentRun.id == created.json()["run_id"])
    )
    assert run is not None
    run.status = "failed"
    run.error_code = "TEST_TERMINAL"
    await db_session.flush()

    deleted = await api_client.delete(
        f"/api/v1/interviews/{created.json()['interview_id']}", headers=bearer(token)
    )
    missing = await api_client.get(
        f"/api/v1/interviews/{created.json()['interview_id']}", headers=bearer(token)
    )

    assert deleted.status_code == HTTPStatus.NO_CONTENT
    assert missing.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_other_user_cannot_reference_or_read_interview_resources(
    api_client: AsyncClient,
) -> None:
    token_a, _, _ = await guest_login(api_client)
    token_b, _, _ = await guest_login(api_client)
    resume_id, target_id = await create_materials(api_client, token_a, "isolated")
    forbidden_create = await api_client.post(
        "/api/v1/interviews",
        json={
            "resume_version_id": resume_id,
            "job_target_id": target_id,
            "interview_type": "resume_deep_dive",
        },
        headers={**bearer(token_b), "Idempotency-Key": "foreign-materials"},
    )
    created = await api_client.post(
        "/api/v1/interviews",
        json={
            "resume_version_id": resume_id,
            "job_target_id": target_id,
            "interview_type": "resume_deep_dive",
        },
        headers={**bearer(token_a), "Idempotency-Key": "own-session"},
    )
    hidden = await api_client.get(
        f"/api/v1/interviews/{created.json()['interview_id']}",
        headers=bearer(token_b),
    )
    assert forbidden_create.status_code == HTTPStatus.NOT_FOUND
    assert created.status_code == HTTPStatus.ACCEPTED
    assert hidden.status_code == HTTPStatus.NOT_FOUND
