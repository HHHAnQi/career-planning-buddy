"""Shared API and state-machine enumerations."""

from enum import StrEnum


class GoalType(StrEnum):
    AI_BACKEND = "ai_backend"
    AGENT_APP = "agent_app"
    BACKEND_JAVA = "backend_java"
    DATA_ENGINEER = "data_engineer"
    FULLSTACK = "fullstack"
    OTHER = "other"


class CareerStage(StrEnum):
    EXPLORING = "exploring"
    PREPARING = "preparing"
    APPLYING = "applying"
    INTERVIEWING = "interviewing"


class SkillLevel(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunResultKind(StrEnum):
    PLAN = "plan"
    CLARIFICATION = "clarification"
    SAFE_RESPONSE = "safe_response"
    NAVIGATION = "navigation"
    INTERVIEW_TURN = "interview_turn"
    INTERVIEW_REPORT = "interview_report"
    RESUME_ASSESSMENT = "resume_assessment"
    RESUME_OPTIMIZATION = "resume_optimization"


class PlanStatus(StrEnum):
    GENERATED = "generated"
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    EXPIRED = "expired"


class TaskType(StrEnum):
    LEARNING = "learning"
    PROJECT = "project"
    INTERVIEW = "interview"
    APPLICATION = "application"
    RESUME = "resume"
    OTHER = "other"


class AbandonedReason(StrEnum):
    TOO_HARD = "too_hard"
    TOO_EASY = "too_easy"
    NO_TIME = "no_time"
    LOST_INTEREST = "lost_interest"
    BLOCKED = "blocked"
    OTHER = "other"


class NextPlanAction(StrEnum):
    CONTINUE = "continue"
    ADJUST = "adjust"


class RunIntent(StrEnum):
    CREATE_PLAN = "create_plan"
    REPLAN = "replan"
    NAVIGATE = "navigate"
    UNSUPPORTED = "unsupported"
    INTERVIEW_START = "interview_start"
    INTERVIEW_ANSWER = "interview_answer"
    INTERVIEW_REPORT = "interview_report"
    RESUME_ASSESSMENT = "resume_assessment"
    RESUME_OPTIMIZATION = "resume_optimization"


class RunKind(StrEnum):
    PLANNING = "planning"
    INTERVIEW_START = "interview_start"
    INTERVIEW_ANSWER = "interview_answer"
    INTERVIEW_REPORT = "interview_report"
    RESUME_ASSESSMENT = "resume_assessment"
    RESUME_OPTIMIZATION = "resume_optimization"


class ReplanMode(StrEnum):
    INITIAL = "initial"
    CONTINUE = "continue"
    ADJUST = "adjust"


class GoalBriefStatus(StrEnum):
    CLARIFICATION_REQUIRED = "clarification_required"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class ObjectiveType(StrEnum):
    CAREER_PLAN = "career_plan"
    PROJECT = "project"
    APPLICATION = "application"
    INTERVIEW = "interview"
    SKILL_TRANSITION = "skill_transition"
