"""Stage 1 enum and profile contract tests."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.auth import GuestLoginRequest
from app.schemas.enums import (
    CareerStage,
    GoalType,
    PlanStatus,
    RunResultKind,
    RunStatus,
    SkillLevel,
    TaskStatus,
)
from app.schemas.profile import ProfilePatchRequest, ProfilePutRequest, ProfileResponse


def test_stage_one_enums_match_contract_values() -> None:
    assert [item.value for item in GoalType] == [
        "ai_backend",
        "agent_app",
        "backend_java",
        "data_engineer",
        "fullstack",
        "other",
    ]
    assert [item.value for item in CareerStage] == [
        "exploring",
        "preparing",
        "applying",
        "interviewing",
    ]
    assert [item.value for item in SkillLevel] == ["beginner", "intermediate", "advanced"]
    assert [item.value for item in RunStatus] == [
        "pending",
        "running",
        "completed",
        "degraded",
        "failed",
        "cancelled",
    ]
    assert [item.value for item in RunResultKind] == [
        "plan",
        "clarification",
        "safe_response",
        "navigation",
        "interview_turn",
        "interview_report",
        "resume_assessment",
        "resume_optimization",
    ]
    assert [item.value for item in PlanStatus] == [
        "generated",
        "active",
        "completed",
        "archived",
    ]
    assert [item.value for item in TaskStatus] == [
        "pending",
        "in_progress",
        "completed",
        "abandoned",
        "expired",
    ]


def test_guest_device_id_length_is_bounded() -> None:
    with pytest.raises(ValidationError):
        GuestLoginRequest(device_id="short")


def test_profile_rejects_unknown_user_id() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ProfilePutRequest.model_validate(
            {
                "user_id": "3f42b5fa-16b8-45d4-a095-3c2d5dc1a35b",
                "goal_type": "agent_app",
                "stage": "preparing",
                "time_budget_minutes": 120,
                "skill_level": "intermediate",
            }
        )


@pytest.mark.parametrize("minutes", [14, 481])
def test_profile_time_budget_is_bounded(minutes: int) -> None:
    with pytest.raises(ValidationError):
        ProfilePutRequest(
            goal_type=GoalType.AGENT_APP,
            stage=CareerStage.PREPARING,
            time_budget_minutes=minutes,
            skill_level=SkillLevel.INTERMEDIATE,
            start_date=datetime.now(UTC).date(),
            deadline=datetime.now(UTC).date() + timedelta(days=27),
        )


def test_profile_deadline_cannot_be_in_the_past() -> None:
    with pytest.raises(ValidationError, match="deadline cannot be earlier"):
        ProfilePutRequest(
            goal_type=GoalType.AGENT_APP,
            stage=CareerStage.PREPARING,
            time_budget_minutes=120,
            skill_level=SkillLevel.INTERMEDIATE,
            start_date=datetime.now(UTC).date(),
            deadline=datetime.now(UTC).date() - timedelta(days=1),
        )


def test_profile_dates_are_required_and_period_is_bounded_to_eight_weeks() -> None:
    base = {
        "goal_type": "agent_app",
        "stage": "preparing",
        "time_budget_minutes": 120,
        "skill_level": "intermediate",
        "start_date": datetime.now(UTC).date().isoformat(),
    }
    with pytest.raises(ValidationError, match="Field required"):
        ProfilePutRequest.model_validate(base)
    with pytest.raises(ValidationError, match="planning period cannot be more than 8 weeks"):
        ProfilePutRequest.model_validate(
            {
                **base,
                "deadline": (datetime.now(UTC).date() + timedelta(days=56)).isoformat(),
            }
        )


def test_profile_response_can_read_an_expired_historical_deadline() -> None:
    response = ProfileResponse(
        goal_type=GoalType.AGENT_APP,
        stage=CareerStage.PREPARING,
        time_budget_minutes=120,
        skill_level=SkillLevel.INTERMEDIATE,
        start_date=datetime.now(UTC).date() - timedelta(days=2),
        deadline=datetime.now(UTC).date() - timedelta(days=1),
        version=1,
    )

    assert response.deadline is not None
    assert response.deadline < datetime.now(UTC).date()


def test_profile_patch_cannot_clear_deadline() -> None:
    with pytest.raises(ValidationError, match="cannot be cleared"):
        ProfilePatchRequest.model_validate({"version": 1, "deadline": None})


def test_profile_period_requires_start_before_end() -> None:
    with pytest.raises(ValidationError, match="start_date must be on or before"):
        ProfilePutRequest(
            goal_type=GoalType.AGENT_APP,
            stage=CareerStage.PREPARING,
            time_budget_minutes=120,
            skill_level=SkillLevel.INTERMEDIATE,
            start_date=datetime.now(UTC).date() + timedelta(days=2),
            deadline=datetime.now(UTC).date() + timedelta(days=1),
        )
    with pytest.raises(ValidationError, match="start_date is required"):
        ProfilePatchRequest.model_validate({"version": 1, "start_date": None})


def test_profile_preferences_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ProfilePutRequest.model_validate(
            {
                "goal_type": "agent_app",
                "stage": "preparing",
                "time_budget_minutes": 120,
                "skill_level": "intermediate",
                "preferences": {"unknown": True},
            }
        )


def test_profile_patch_requires_an_update() -> None:
    with pytest.raises(ValidationError, match="at least one profile field"):
        ProfilePatchRequest(version=1)
