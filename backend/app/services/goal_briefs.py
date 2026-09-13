"""Goal clarification, deterministic sufficiency policy, and confirmation gate."""

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from hashlib import sha256
from http import HTTPStatus
from uuid import UUID

import httpx
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.errors import AgentError
from app.core.database import session_transaction
from app.core.exceptions import AppError
from app.core.time import product_today
from app.models.goal_brief import GoalBrief
from app.models.user_profile import UserProfile
from app.providers.goal_understanding import (
    GoalUnderstandingProvider,
    RuleGoalUnderstandingProvider,
    classify_objective_type,
)
from app.repositories.goal_briefs import GoalBriefRepository
from app.repositories.profiles import ProfileRepository
from app.schemas.agent_runs import AgentRunCreatedResponse
from app.schemas.enums import GoalBriefStatus, GoalType, ObjectiveType, RunStatus
from app.schemas.goal_briefs import (
    GoalBriefConfirmResponse,
    GoalBriefCreateRequest,
    GoalBriefRefineRequest,
    GoalBriefResponse,
    GoalExtraction,
)
from app.services.agent_runs import AgentRunService
from app.services.input_safety import assess_input_risk

GOAL_LABELS = {
    GoalType.AI_BACKEND.value: "AI 后端工程师",
    GoalType.AGENT_APP.value: "Agent 应用工程师",
    GoalType.BACKEND_JAVA.value: "Java 后端工程师",
    GoalType.DATA_ENGINEER.value: "数据工程师",
    GoalType.FULLSTACK.value: "全栈工程师",
}

OBJECTIVE_DEFAULTS: dict[ObjectiveType, dict[str, list[str]]] = {
    ObjectiveType.CAREER_PLAN: {
        "capability_focus": ["岗位定位", "求职材料", "行动反馈"],
        "tech_stack": ["根据目标岗位和现有能力确定"],
        "deliverables": ["目标岗位清单", "阶段行动计划", "求职进展记录"],
        "success_criteria": ["方向明确", "行动可执行", "进展可复盘"],
    },
    ObjectiveType.PROJECT: {
        "capability_focus": ["岗位核心能力", "可展示的项目交付", "项目表达与复盘"],
        "tech_stack": ["由系统根据目标岗位和现有能力推荐"],
        "deliverables": ["可运行的项目成果", "README 与架构说明", "可用于简历的项目描述"],
        "success_criteria": ["核心流程可演示", "成果可被验证", "能够说明关键设计取舍"],
    },
    ObjectiveType.APPLICATION: {
        "capability_focus": ["岗位筛选", "材料匹配", "投递反馈"],
        "tech_stack": ["根据目标岗位要求核对"],
        "deliverables": ["目标岗位清单", "定制版简历", "投递与反馈跟踪表"],
        "success_criteria": ["岗位与方向匹配", "材料完成针对性调整", "投递状态可追踪"],
    },
    ObjectiveType.INTERVIEW: {
        "capability_focus": ["岗位知识", "项目表达", "模拟与复盘"],
        "tech_stack": ["根据目标岗位和面试范围确定"],
        "deliverables": ["面试范围清单", "高频问题答案", "模拟面试复盘"],
        "success_criteria": ["重点覆盖完整", "回答有证据", "薄弱项经过复测"],
    },
    ObjectiveType.SKILL_TRANSITION: {
        "capability_focus": ["能力差距", "学习实践", "成果验证"],
        "tech_stack": ["根据转型方向和现有能力推荐"],
        "deliverables": ["能力差距清单", "学习实践成果", "阶段复盘记录"],
        "success_criteria": ["差距有优先级", "学习形成实践产物", "阶段结果可验证"],
    },
}

OBJECTIVE_QUESTIONS = {
    ObjectiveType.CAREER_PLAN: "你这次最希望解决求职规划中的哪个具体问题？",
    ObjectiveType.PROJECT: "你希望设计哪类项目，或它要解决什么具体问题？",
    ObjectiveType.APPLICATION: "你希望优先投递哪类岗位，当前最需要解决什么投递问题？",
    ObjectiveType.INTERVIEW: "你准备的是哪类面试，预计重点解决什么问题？",
    ObjectiveType.SKILL_TRANSITION: "你希望补齐或转向哪项能力，目标结果是什么？",
}


class GoalBriefService:
    def __init__(
        self, session: AsyncSession, provider: GoalUnderstandingProvider, runs: AgentRunService
    ) -> None:
        self._session = session
        self._provider = provider
        self._runs_service = runs
        self._briefs = GoalBriefRepository(session)
        self._profiles = ProfileRepository(session)

    async def create(
        self, *, user_id: UUID, payload: GoalBriefCreateRequest, idempotency_key: str
    ) -> GoalBrief:
        self._ensure_safe_for_external_processing(payload.message)
        request_hash = self._hash(payload.model_dump(mode="json"))
        async with session_transaction(self._session):
            existing = await self._briefs.get_by_idempotency(user_id, idempotency_key)
            if existing is not None:
                self._validate_idempotency(existing, request_hash)
                return existing
            profile = await self._profiles.get_for_user(user_id)
            if profile is None:
                raise AppError(
                    code="VALIDATION_PROFILE_REQUIRED",
                    message="complete the career profile before defining a plan",
                    status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                )
            if (
                profile.start_date is None
                or profile.deadline is None
                or profile.start_date > profile.deadline
                or profile.deadline < product_today()
            ):
                raise AppError(
                    code="VALIDATION_PROFILE_DEADLINE_REQUIRED",
                    message="set a current target date before defining a plan",
                    status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                )
        extraction, method, model_id = await self._extract(payload.message, profile)
        try:
            async with session_transaction(self._session):
                existing = await self._briefs.get_by_idempotency(user_id, idempotency_key)
                if existing is not None:
                    self._validate_idempotency(existing, request_hash)
                    return existing
                active = await self._briefs.get_active_for_user(user_id)
                if active is not None:
                    raise AppError(
                        code="STATE_GOAL_BRIEF_ALREADY_ACTIVE",
                        message="finish or cancel the current goal draft first",
                        status_code=HTTPStatus.CONFLICT,
                    )
                brief = GoalBrief(
                    user_id=user_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    source_message=payload.message.strip(),
                    hint_intent=payload.hint_intent,
                    source_plan_id=payload.source_plan_id,
                    extraction_method=method,
                    model_id=model_id,
                    **self._complete(extraction.model_dump(), profile, payload.message),
                )
                await self._briefs.create(brief)
        except IntegrityError as exc:
            raise AppError(
                code="STATE_GOAL_BRIEF_ALREADY_ACTIVE",
                message="finish or cancel the current goal draft first",
                status_code=HTTPStatus.CONFLICT,
            ) from exc
        return brief

    async def get(self, brief_id: UUID, user_id: UUID) -> GoalBrief:
        brief = await self._briefs.get_for_user(brief_id, user_id)
        if brief is None:
            raise AppError(
                code="NOT_FOUND_GOAL_BRIEF",
                message="goal draft was not found",
                status_code=HTTPStatus.NOT_FOUND,
            )
        return brief

    async def refine(
        self, *, brief_id: UUID, user_id: UUID, payload: GoalBriefRefineRequest
    ) -> GoalBrief:
        self._ensure_safe_for_external_processing(payload.message)
        profile = await self._profiles.get_for_user(user_id)
        if profile is None:
            raise AppError(
                code="VALIDATION_PROFILE_REQUIRED",
                message="complete the career profile before refining a plan",
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            )
        extraction, method, model_id = await self._extract(payload.message, profile)
        async with session_transaction(self._session):
            brief = await self._require_mutable(brief_id, user_id, payload.version)
            current = {
                "objective_type": extraction.objective_type or brief.objective_type,
                "target_role": extraction.target_role or brief.target_role,
                "objective": extraction.objective or brief.objective,
                "capability_focus": extraction.capability_focus or brief.capability_focus_json,
                "tech_stack": extraction.tech_stack or brief.tech_stack_json,
                "duration_weeks": extraction.duration_weeks or brief.duration_weeks,
                "deliverables": extraction.deliverables or brief.deliverables_json,
                "success_criteria": extraction.success_criteria or brief.success_criteria_json,
            }
            values = self._complete(
                current, profile, f"{brief.source_message}\n补充：{payload.message.strip()}"
            )
            brief.source_message = f"{brief.source_message}\n补充：{payload.message.strip()}"
            self._assign(brief, values)
            brief.extraction_method = method
            brief.model_id = model_id
            brief.version += 1
            brief.updated_at = datetime.now(UTC)
        return brief

    async def confirm(
        self, *, brief_id: UUID, user_id: UUID, version: int
    ) -> GoalBriefConfirmResponse:
        run = None
        should_schedule = False
        async with session_transaction(self._session):
            brief = await self._briefs.get_for_user(brief_id, user_id, for_update=True)
            if brief is None:
                raise AppError(
                    code="NOT_FOUND_GOAL_BRIEF",
                    message="goal draft was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            if brief.status == GoalBriefStatus.CONFIRMED.value:
                run = await self._runs_service.get_by_goal_brief(brief.id, user_id)
                if run is None:
                    raise AppError(
                        code="STATE_GOAL_BRIEF_RUN_MISSING",
                        message="confirmed goal has no Agent Run",
                        status_code=HTTPStatus.CONFLICT,
                    )
            else:
                self._check_version(brief, version)
                if brief.status != GoalBriefStatus.AWAITING_CONFIRMATION.value:
                    raise AppError(
                        code="STATE_GOAL_BRIEF_NOT_READY",
                        message="answer the remaining goal questions before confirmation",
                        status_code=HTTPStatus.CONFLICT,
                    )
                profile = await self._profiles.get_for_user(user_id)
                if (
                    profile is None
                    or profile.start_date is None
                    or profile.deadline is None
                    or profile.start_date > profile.deadline
                    or profile.deadline < product_today()
                ):
                    raise AppError(
                        code="VALIDATION_PROFILE_DEADLINE_REQUIRED",
                        message="set a current target date before confirming the plan",
                        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    )
                run = await self._runs_service.create(
                    user_id=user_id,
                    message=self._planning_message(brief, profile),
                    hint_intent=brief.hint_intent,
                    goal_type_override=None,
                    source_plan_id=brief.source_plan_id,
                    idempotency_key=f"goal-{brief.id}",
                    goal_brief_id=brief.id,
                    schedule=False,
                )
                brief.status = GoalBriefStatus.CONFIRMED.value
                brief.confirmed_at = datetime.now(UTC)
                brief.updated_at = datetime.now(UTC)
                brief.version += 1
                should_schedule = True
        assert run is not None
        if should_schedule:
            self._runs_service.schedule(run.id)
        return GoalBriefConfirmResponse(
            goal_brief=self.to_response(brief),
            run=AgentRunCreatedResponse(
                run_id=run.id,
                status=RunStatus(run.status),
                events_url=f"/api/v1/agent-runs/{run.id}/events",
            ),
        )

    async def cancel(self, *, brief_id: UUID, user_id: UUID, version: int) -> GoalBrief:
        async with session_transaction(self._session):
            brief = await self._require_mutable(brief_id, user_id, version)
            brief.status = GoalBriefStatus.CANCELLED.value
            brief.cancelled_at = datetime.now(UTC)
            brief.updated_at = datetime.now(UTC)
            brief.version += 1
        return brief

    async def _extract(
        self, message: str, profile: UserProfile
    ) -> tuple[GoalExtraction, str, str]:
        _, _, planning_days, _ = self._profile_window(profile)
        try:
            result = await self._provider.extract(
                message,
                planning_days=planning_days,
                daily_budget_minutes=profile.time_budget_minutes,
            )
            return result, self._provider.method, self._provider.model_id
        except (AgentError, httpx.HTTPError, ValueError, KeyError, TypeError, ValidationError):
            fallback = RuleGoalUnderstandingProvider()
            return (
                await fallback.extract(
                    message,
                    planning_days=planning_days,
                    daily_budget_minutes=profile.time_budget_minutes,
                ),
                "rule_fallback",
                fallback.model_id,
            )

    @staticmethod
    def _complete(
        extracted: Mapping[str, object], profile: UserProfile, message: str
    ) -> dict[str, object]:
        assumptions: list[str] = []
        target = extracted.get("target_role") or GOAL_LABELS.get(profile.goal_type)
        raw_objective_type = extracted.get("objective_type") or classify_objective_type(message)
        try:
            objective_type = (
                raw_objective_type
                if isinstance(raw_objective_type, ObjectiveType)
                else ObjectiveType(raw_objective_type)
                if isinstance(raw_objective_type, str)
                else None
            )
        except ValueError:
            objective_type = None
        extracted_objective = extracted.get("objective")
        objective = (
            extracted_objective.strip()
            if isinstance(extracted_objective, str) and extracted_objective.strip()
            else message.strip()
            if objective_type is not None
            else None
        )
        planning_start, planning_end, planning_days, duration = GoalBriefService._profile_window(
            profile
        )
        requested_duration = extracted.get("duration_weeks")
        assumptions.append(
            f"总体周期固定为你选择的 {planning_start.isoformat()} 至 "
            f"{planning_end.isoformat()}，共 {planning_days} 天，不会安排到日期范围之外"
        )
        if isinstance(requested_duration, int) and requested_duration != duration:
            assumptions.append(
                f"你在目标中提到 {requested_duration} 周，但日期范围折算为 {duration} 个周期；"
                "系统以开始和结束日期为准"
            )
        feasibility = extracted.get("feasibility")
        feasibility_reason = extracted.get("feasibility_reason")
        constrained_strategy = extracted.get("constrained_strategy")
        if feasibility in {"tight", "unrealistic"}:
            reason = (
                feasibility_reason.strip()
                if isinstance(feasibility_reason, str) and feasibility_reason.strip()
                else "目标范围与当前时间投入相比偏紧"
            )
            strategy = (
                constrained_strategy.strip()
                if isinstance(constrained_strategy, str) and constrained_strategy.strip()
                else "保留最重要的可验证结果，并压缩非必要范围"
            )
            assumptions.append(
                f"可行性提醒：{reason}。如果你仍确认按期执行，将采用受限方案：{strategy}"
            )
        defaults = OBJECTIVE_DEFAULTS.get(
            objective_type or ObjectiveType.CAREER_PLAN,
            OBJECTIVE_DEFAULTS[ObjectiveType.CAREER_PLAN],
        )
        capability = GoalBriefService._string_list(extracted.get("capability_focus"))
        if not capability:
            capability = defaults["capability_focus"]
            assumptions.append("能力重点已根据本次目标类型推荐")
        stack = GoalBriefService._string_list(extracted.get("tech_stack"))
        if not stack:
            stack = defaults["tech_stack"]
            assumptions.append("相关技能或技术范围将在计划阶段结合岗位确定")
        deliverables = GoalBriefService._string_list(extracted.get("deliverables"))
        if not deliverables:
            deliverables = defaults["deliverables"]
            assumptions.append("交付物已根据本次目标类型推荐")
        criteria = GoalBriefService._string_list(extracted.get("success_criteria"))
        if not criteria:
            criteria = defaults["success_criteria"]
        missing: list[str] = []
        questions: list[str] = []
        if objective_type is None:
            missing.append("objective_type")
            questions.append("你希望重点推进求职规划、项目、投递、面试，还是技能转型？")
        if not target:
            missing.append("target_role")
            questions.append("这次目标主要面向什么岗位或岗位方向？")
        if not objective:
            missing.append("objective")
            questions.append(
                OBJECTIVE_QUESTIONS.get(
                    objective_type or ObjectiveType.CAREER_PLAN,
                    "你这次希望达成什么具体结果？",
                )
            )
        return {
            "status": GoalBriefStatus.CLARIFICATION_REQUIRED.value
            if missing
            else GoalBriefStatus.AWAITING_CONFIRMATION.value,
            "objective_type": objective_type.value if objective_type is not None else None,
            "target_role": target,
            "objective": objective,
            "capability_focus_json": capability,
            "tech_stack_json": stack,
            "duration_weeks": duration,
            "deliverables_json": deliverables,
            "success_criteria_json": criteria,
            "assumptions_json": assumptions,
            "missing_fields_json": missing,
            "questions_json": questions,
        }

    @staticmethod
    def _string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str)]

    @staticmethod
    def _assign(brief: GoalBrief, values: dict[str, object]) -> None:
        for key, value in values.items():
            setattr(brief, key, value)

    async def _require_mutable(self, brief_id: UUID, user_id: UUID, version: int) -> GoalBrief:
        brief = await self._briefs.get_for_user(brief_id, user_id, for_update=True)
        if brief is None:
            raise AppError(
                code="NOT_FOUND_GOAL_BRIEF",
                message="goal draft was not found",
                status_code=HTTPStatus.NOT_FOUND,
            )
        self._check_version(brief, version)
        if brief.status not in {
            GoalBriefStatus.CLARIFICATION_REQUIRED.value,
            GoalBriefStatus.AWAITING_CONFIRMATION.value,
        }:
            raise AppError(
                code="STATE_GOAL_BRIEF_FINAL",
                message="goal draft is already final",
                status_code=HTTPStatus.CONFLICT,
            )
        return brief

    @staticmethod
    def _check_version(brief: GoalBrief, version: int) -> None:
        if brief.version != version:
            raise AppError(
                code="STATE_VERSION_CONFLICT",
                message="goal draft changed; refresh and retry",
                status_code=HTTPStatus.CONFLICT,
            )

    @staticmethod
    def _hash(payload: dict[str, object]) -> str:
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _validate_idempotency(brief: GoalBrief, request_hash: str) -> None:
        if brief.request_hash != request_hash:
            raise AppError(
                code="STATE_IDEMPOTENCY_KEY_REUSED",
                message="Idempotency-Key was already used with another request",
                status_code=HTTPStatus.CONFLICT,
            )

    @staticmethod
    def _planning_message(brief: GoalBrief, profile: UserProfile) -> str:
        capability_focus = "、".join(brief.capability_focus_json)
        tech_stack = "、".join(brief.tech_stack_json)
        deliverables = "、".join(brief.deliverables_json)
        success_criteria = "、".join(brief.success_criteria_json)
        objective_label = {
            ObjectiveType.CAREER_PLAN.value: "职业规划",
            ObjectiveType.PROJECT.value: "项目设计",
            ObjectiveType.APPLICATION.value: "岗位投递",
            ObjectiveType.INTERVIEW.value: "面试准备",
            ObjectiveType.SKILL_TRANSITION.value: "技能转型",
        }.get(brief.objective_type or "", "职业目标")
        planning_start, planning_end, planning_days, duration = GoalBriefService._profile_window(
            profile
        )
        return (
            f"已由用户确认的{objective_label}目标：面向{brief.target_role}，{brief.objective}。"
            f"用户确认的硬日期边界为 {planning_start.isoformat()} 至 "
            f"{planning_end.isoformat()}，共 {planning_days} 天、{duration} 个周期；"
            f"能力重点：{capability_focus}；"
            f"技术栈：{tech_stack}；交付物：{deliverables}；"
            f"成功标准：{success_criteria}。"
            "请生成截至目标日期的总体路线，并展开从开始日期起最多 7 天的具体任务；"
            "最后不足 7 天时不得越过目标日期补位。"
        )

    @staticmethod
    def _profile_window(profile: UserProfile) -> tuple[date, date, int, int]:
        if profile.start_date is None or profile.deadline is None:
            raise ValueError("profile planning dates are required")
        planning_start = max(product_today(), profile.start_date)
        planning_end = profile.deadline
        planning_days = max((planning_end - planning_start).days + 1, 1)
        duration = max(1, min(8, (planning_days + 6) // 7))
        return planning_start, planning_end, planning_days, duration

    @staticmethod
    def to_response(brief: GoalBrief) -> GoalBriefResponse:
        return GoalBriefResponse(
            goal_brief_id=brief.id,
            status=GoalBriefStatus(brief.status),
            source_message=brief.source_message,
            hint_intent=brief.hint_intent,
            source_plan_id=brief.source_plan_id,
            objective_type=(
                ObjectiveType(brief.objective_type) if brief.objective_type is not None else None
            ),
            target_role=brief.target_role,
            objective=brief.objective,
            capability_focus=brief.capability_focus_json,
            tech_stack=brief.tech_stack_json,
            duration_weeks=brief.duration_weeks,
            deliverables=brief.deliverables_json,
            success_criteria=brief.success_criteria_json,
            assumptions=brief.assumptions_json,
            missing_fields=brief.missing_fields_json,
            questions=brief.questions_json,
            extraction_method=brief.extraction_method,
            model_id=brief.model_id,
            version=brief.version,
            created_at=brief.created_at,
            updated_at=brief.updated_at,
        )

    @staticmethod
    def _ensure_safe_for_external_processing(message: str) -> None:
        risk = assess_input_risk(message)
        if risk.level == "high":
            raise AppError(
                code="SAFETY_HIGH_RISK_INPUT",
                message=(
                    "现在最重要的是先确保你的安全。请尽快联系身边可信任的人或当地紧急服务；"
                    "本服务不提供医疗诊断或紧急救援。"
                ),
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            )
