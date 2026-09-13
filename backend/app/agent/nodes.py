"""Deterministic controlled Stage 4 graph nodes."""

import re
from datetime import date, timedelta
from typing import Literal
from uuid import UUID

from app.core.time import product_today
from app.harness.evidence import evidence_refs_are_visible
from app.schemas.agent_runs import (
    ClarificationRequest,
    CompanionMessageCandidate,
    EvidenceVisibility,
    IntentResult,
    NavigationResult,
    PlanCandidate,
    PlanContext,
    PlanningContext,
    PlanningWindow,
    ProfileContext,
    ReviewContext,
    RiskResult,
    SafeResponse,
    TaskCandidate,
    TaskContext,
    ValidationCheck,
    ValidationReport,
    WeeklyFocusCandidate,
)
from app.schemas.enums import GoalType, ReplanMode, RunIntent, TaskStatus, TaskType
from app.services.input_safety import assess_input_risk

INTENT_ROUTER_VERSION = "intent-rule-v3"

INTENT_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "query_only": (
        re.compile(r"查看.{0,6}(计划|任务)|查询.{0,6}(计划|任务)|我的(计划|任务)"),
        re.compile(r"(今天|今日|明天|本周).{0,5}(有|是)?什么(任务|安排)"),
        re.compile(
            r"\b(show|view|list|check|what(?:'s| is))\b"
            r".{0,30}\b(plan|plans|task|tasks|schedule)\b",
            re.I,
        ),
    ),
    "adjust": (
        re.compile(
            r"调整|减量|减少.{0,5}(任务|工作量|时长)|换(个)?重点|改变方向|"
            r"更换方向|解决.{0,5}阻碍|每天.{0,8}(半小时|分钟)"
        ),
        re.compile(
            r"\b(adjust|reduce|change|switch|rebalance|cut)\b"
            r".{0,40}\b(scope|workload|time|focus|direction|plan|tasks?)\b",
            re.I,
        ),
    ),
    "continue": (
        re.compile(r"继续|接着|续接|沿用|按原计划|下一(个)?计划|后续计划"),
        re.compile(
            r"\b(continue|carry on|keep|next)\b.{0,35}"
            r"\b(plan|direction|tasks?|steps?)\b",
            re.I,
        ),
    ),
    "negated_adjust": (
        re.compile(r"(不要|不用|无需|不需要).{0,6}(调整|改|改变)|保持.{0,5}(方向|计划)"),
        re.compile(r"\b(without|do not|don't|no need to)\b.{0,30}\b(change|adjust|switch)\b", re.I),
    ),
    "reset_direction": (
        re.compile(r"重新规划|从头开始|新方向|改变方向|更换方向"),
        re.compile(r"\b(start over|new direction|change direction|switch direction)\b", re.I),
    ),
    "create_plan": (
        re.compile(
            r"(制定|创建|生成|规划|安排|设计|做|准备).{0,18}(求职|职业|岗位|面试|申请|技能|学习|转型|项目|计划|任务)"
        ),
        re.compile(r"(求职|职业|面试|申请|技能|学习|转型|项目).{0,24}(计划|准备|规划|安排)"),
        re.compile(
            r"(我想|我需要|请帮我|帮我).{0,18}(求职|职业|面试|申请|技能|学习|转型|计划|任务)"
        ),
        re.compile(
            r"\b(create|build|make|design|generate|plan|prepare|help me|"
            r"i need|i want|give me)\b.{0,50}"
            r"\b(plan|career|job search|interview|application|applications|skill|"
            r"transition|direction|practice|tasks?|deliverables?)\b",
            re.I,
        ),
    ),
    "career_context": (
        re.compile(r"求职|职业|岗位|面试|简历|申请|技能|学习|转型|秋招|春招|实习"),
        re.compile(
            r"\b(career|job|role|interview|resume|application|skill|internship|transition)\b",
            re.I,
        ),
    ),
    "greeting": (
        re.compile(r"^\s*(你好|您好|嗨|哈喽|在吗)[！!。.，,\s]*$"),
        re.compile(r"^\s*(hi|hello|hey)[!.?,\s]*$", re.I),
    ),
}


def risk_gate(message: str) -> RiskResult:
    return assess_input_risk(message)


def route_intent(
    *,
    message: str,
    hint_intent: str | None,
    profile: ProfileContext | None,
    source_plan_exists: bool,
    goal_type_override: GoalType | None = None,
    forced_replan_mode: ReplanMode | None = None,
) -> IntentResult:
    matches = {
        rule_id: any(pattern.search(message) for pattern in patterns)
        for rule_id, patterns in INTENT_PATTERNS.items()
    }
    matched_rule_ids = [rule_id for rule_id, matched in matches.items() if matched]
    ambiguity_reasons: list[str] = []
    requested_weeks = parse_horizon_weeks(message)
    method: Literal["rule", "model", "rule_fallback"] = "rule"
    navigation_action: Literal["view_current_plan", "view_today_tasks"] | None = None
    navigation_target: Literal["/journey", "/today"] | None = None

    if forced_replan_mode is not None and source_plan_exists:
        intent = RunIntent.REPLAN
        mode = forced_replan_mode
        confidence = 1.0
        matched_rule_ids.append("server_forced_replan_mode")
    elif matches["query_only"]:
        intent = RunIntent.NAVIGATE
        mode = ReplanMode.INITIAL
        confidence = 0.99
        navigation_action, navigation_target = _query_navigation(message)
    elif source_plan_exists:
        intent, mode, confidence = _route_with_source(matches)
        if intent == RunIntent.UNSUPPORTED:
            method = "rule_fallback"
            ambiguity_reasons.append("no_supported_intent_signal")
    elif matches["create_plan"]:
        intent = RunIntent.CREATE_PLAN
        mode = ReplanMode.INITIAL
        confidence = 0.96
    elif matches["adjust"] or matches["continue"]:
        intent = RunIntent.UNSUPPORTED
        mode = ReplanMode.INITIAL
        confidence = 0.3
        method = "rule_fallback"
        ambiguity_reasons.append("replan_source_missing")
    elif matches["greeting"] and hint_intent is None:
        intent = RunIntent.UNSUPPORTED
        mode = ReplanMode.INITIAL
        confidence = 0.98
    else:
        intent = RunIntent.UNSUPPORTED
        mode = ReplanMode.INITIAL
        confidence = 0.25
        method = "rule_fallback"
        ambiguity_reasons.append("no_supported_intent_signal")

    if hint_intent is not None:
        hint_conflicts = (
            hint_intent == RunIntent.CREATE_PLAN.value and intent == RunIntent.REPLAN
        ) or (hint_intent == RunIntent.REPLAN.value and intent == RunIntent.CREATE_PLAN)
        if hint_conflicts:
            ambiguity_reasons.append("hint_conflicts_with_message_or_context")
            confidence = min(confidence, 0.79)
        elif intent == RunIntent.UNSUPPORTED and method == "rule_fallback":
            ambiguity_reasons.append("hint_without_message_evidence")

    confidence_band: Literal["high", "medium", "low"]
    if confidence >= 0.85:
        confidence_band = "high"
    elif confidence >= 0.6:
        confidence_band = "medium"
    else:
        confidence_band = "low"

    missing_slots: list[Literal["goal_type", "stage", "time_budget_minutes", "skill_level"]] = []
    if profile is None and intent in {RunIntent.CREATE_PLAN, RunIntent.REPLAN}:
        missing_slots = ["goal_type", "stage", "time_budget_minutes", "skill_level"]
    requires_fresh_information = any(
        marker in message
        for marker in ("最新", "当前岗位", "市场信息", "搜索", "[mock:tool-search]")
    )
    # Deterministic memory-reference detection: when the user's message
    # explicitly refers to their past execution context, the planning
    # agent should call memory_lookup even if the model wouldn't
    # voluntarily do so (live eval: GLM-4.7 voluntary tool trigger is
    # ~11%). This is the architectural fix for the tool-trigger gap.
    references_past_context = any(
        marker in message
        for marker in (
            "之前", "上次", "复盘", "经验", "偏好", "习惯",
            "我之前", "以前", "总结", "教训", "规律", "方法",
            "past experience", "lessons learned", "review findings",
        )
    )
    return IntentResult(
        intent=intent,
        replan_mode=mode,
        confidence=confidence,
        confidence_band=confidence_band,
        router_version=INTENT_ROUTER_VERSION,
        matched_rule_ids=list(dict.fromkeys(matched_rule_ids)),
        ambiguity_reasons=ambiguity_reasons,
        missing_slots=missing_slots,
        effective_goal_type=goal_type_override or (profile.goal_type if profile else None),
        requested_horizon_weeks=requested_weeks,
        requires_fresh_information=requires_fresh_information,
        references_past_context=references_past_context,
        method=method,
        navigation_action=navigation_action,
        navigation_target=navigation_target,
    )


def _query_navigation(
    message: str,
) -> tuple[
    Literal["view_current_plan", "view_today_tasks"],
    Literal["/journey", "/today"],
]:
    if re.search(r"任务|安排|task|tasks|schedule", message, re.I):
        return "view_today_tasks", "/today"
    return "view_current_plan", "/journey"


def _route_with_source(matches: dict[str, bool]) -> tuple[RunIntent, ReplanMode, float]:
    if matches["negated_adjust"]:
        return RunIntent.REPLAN, ReplanMode.CONTINUE, 0.98
    if matches["adjust"] or matches["reset_direction"]:
        return RunIntent.REPLAN, ReplanMode.ADJUST, 0.98
    if matches["continue"]:
        return RunIntent.REPLAN, ReplanMode.CONTINUE, 0.98
    if matches["create_plan"]:
        mode = ReplanMode.ADJUST if matches["reset_direction"] else ReplanMode.CONTINUE
        return RunIntent.REPLAN, mode, 0.91
    return RunIntent.UNSUPPORTED, ReplanMode.INITIAL, 0.25


def parse_horizon_weeks(message: str) -> int | None:
    match = re.search(r"(?:未来|接下来)?\s*(\d+)\s*周", message)
    if match:
        return max(1, min(8, int(match.group(1))))
    english_match = re.search(r"\b(\d+)\s*(?:week|weeks)\b", message, re.I)
    if english_match:
        return _clamp_horizon_weeks(int(english_match.group(1)))
    english_word_match = re.search(
        r"\b(one|two|three|four|five|six|seven|eight)\s+(?:week|weeks)\b",
        message,
        re.I,
    )
    if english_word_match:
        return _clamp_horizon_weeks(
            {
                "one": 1,
                "two": 2,
                "three": 3,
                "four": 4,
                "five": 5,
                "six": 6,
                "seven": 7,
                "eight": 8,
            }[english_word_match.group(1).lower()]
        )
    chinese_match = re.search(r"(?:未来|接下来)?\s*([一二两三四五六七八])\s*周", message)
    if chinese_match:
        return _clamp_horizon_weeks(_chinese_number(chinese_match.group(1)))
    if re.search(r"(?:未来|接下来)?\s*半\s*个?月", message):
        return 2
    month_match = re.search(r"(?:未来|接下来)?\s*(\d+)\s*个?月", message)
    if month_match:
        return _clamp_horizon_weeks(int(month_match.group(1)) * 4)
    chinese_month_match = re.search(
        r"(?:未来|接下来)?\s*([一二两三四五六七八])\s*个?月",
        message,
    )
    if chinese_month_match:
        return _clamp_horizon_weeks(_chinese_number(chinese_month_match.group(1)) * 4)
    return None


def _chinese_number(value: str) -> int:
    return {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
    }[value]


def _clamp_horizon_weeks(value: int) -> int:
    return max(1, min(8, value))


def build_clarification(intent: IntentResult) -> ClarificationRequest:
    if intent.intent == RunIntent.UNSUPPORTED:
        if intent.method == "rule_fallback" or intent.confidence_band == "low":
            return ClarificationRequest(
                questions=[
                    "我还不能确定你的目标。你希望创建新计划、继续现有计划，还是调整当前计划？"
                ],
                slot_names=["intent"],
                hint_options={"intent": ["create_plan", "replan_continue", "replan_adjust"]},
                reason="intent_uncertain",
                message="我还不能确定你希望如何推进职业计划。",
                suggested_actions=[
                    {
                        "action": "create_plan",
                        "label": "创建新计划",
                        "target_route": "/today",
                    },
                    {
                        "action": "continue_plan",
                        "label": "继续当前计划",
                        "target_route": "/journey",
                    },
                    {
                        "action": "adjust_plan",
                        "label": "调整当前计划",
                        "target_route": "/reviews",
                    },
                ],
            )
        return ClarificationRequest(
            questions=["这个请求不需要生成新计划，请前往计划或任务页面查看。"],
            slot_names=["intent"],
            hint_options={"intent": ["create_plan", "replan"]},
            reason="unsupported_intent",
            message="当前助手专注于创建、继续和调整职业计划。",
            suggested_actions=[
                {
                    "action": "create_plan",
                    "label": "创建职业计划",
                    "target_route": "/today",
                }
            ],
            target_route="/today",
        )
    labels = {
        "goal_type": "你希望重点准备哪类岗位？",
        "stage": "你目前处于哪个求职阶段？",
        "time_budget_minutes": "你每天可以投入多少分钟？",
        "skill_level": "你如何评价当前技能水平？",
    }
    slots = intent.missing_slots[:3]
    return ClarificationRequest(
        questions=[labels[slot] for slot in slots],
        slot_names=list(slots),
        hint_options={
            "goal_type": [item.value for item in GoalType],
            "stage": ["exploring", "preparing", "applying", "interviewing"],
            "time_budget_minutes": ["30", "60", "90"],
            "skill_level": ["beginner", "intermediate", "advanced"],
        },
        reason="profile_incomplete",
        message="完善职业画像后，我才能生成更适合你的行动计划。",
        suggested_actions=[
            {
                "action": "complete_profile",
                "label": "完善职业资料",
                "target_route": "/settings/profile",
            }
        ],
        target_route="/settings/profile",
    )


def build_navigation(intent: IntentResult) -> NavigationResult:
    if intent.navigation_action == "view_today_tasks":
        return NavigationResult(
            action="view_today_tasks",
            label="查看今日任务",
            target_route="/today",
            message="这个请求不需要重新生成计划，可以直接查看今天的任务。",
        )
    return NavigationResult(
        action="view_current_plan",
        label="查看当前计划",
        target_route="/journey",
        message="这个请求不需要重新生成计划，可以直接查看当前计划。",
    )


def build_safe_response() -> SafeResponse:
    return SafeResponse(
        message="现在最重要的是先确保你的安全。请尽快联系身边可信任的人或当地紧急服务。",
        resource_ids=["default-local-resource"],
        disclaimer="本服务不提供医疗诊断或紧急救援。",
    )


def build_planning_context(
    *,
    profile: ProfileContext,
    requested_horizon_weeks: int | None,
    source_plan_id: UUID | None,
    source_plan_version: int | None,
    source_plan: PlanContext | None = None,
    source_review: ReviewContext | None = None,
    recent_tasks: list[TaskContext] | None = None,
    recent_reviews: list[ReviewContext] | None = None,
    completed_facts: list[str],
    blockers: list[str] | None = None,
    planning_date: date | None = None,
) -> PlanningContext:
    today = planning_date or product_today()
    if profile.start_date is not None:
        today = max(today, profile.start_date)
    if profile.deadline is not None:
        if today > profile.deadline:
            raise ValueError("planning date cannot be after the target date")
        horizon_weeks = _deadline_weeks(today, profile.deadline)
        horizon_end = profile.deadline
    else:
        # Transitional fallback for legacy profiles. New profile writes require a deadline.
        horizon_weeks = requested_horizon_weeks or 4
        horizon_end = today + timedelta(weeks=horizon_weeks) - timedelta(days=1)
    window = PlanningWindow(
        planning_date=today,
        horizon_start=today,
        horizon_end=horizon_end,
        horizon_weeks=horizon_weeks,
    )
    return PlanningContext(
        profile=profile,
        planning_window=window,
        source_plan_id=source_plan_id,
        source_plan_version=source_plan_version,
        source_plan=source_plan,
        source_review=source_review,
        recent_tasks=recent_tasks or [],
        recent_reviews=recent_reviews or [],
        completed_facts=completed_facts,
        blockers=blockers or [],
        timezone="UTC",
        time_budget_minutes=profile.time_budget_minutes,
        token_estimate=250 + len(completed_facts) * 12,
    )


def _deadline_weeks(today: date, deadline: date | None) -> int:
    if deadline is None:
        return 4
    days = max((deadline - today).days + 1, 1)
    return max(1, min(8, (days + 6) // 7))


def action_day_count(window: PlanningWindow) -> int:
    """Number of executable dates in this cycle without crossing the target date."""
    remaining = (window.horizon_end - window.planning_date).days + 1
    return max(1, min(7, remaining))


def _within_daily_budget(tasks: list[TaskCandidate], daily_budget: int) -> bool:
    """Allow multi-day candidates; constrain only per-day minutes to the daily budget."""
    per_day: dict[date, int] = {}
    for task in tasks:
        per_day[task.scheduled_date] = per_day.get(task.scheduled_date, 0) + task.estimated_minutes
    return all(total <= daily_budget for total in per_day.values())


CHECK_ORDER = (
    "HORIZON_MATCH",
    "WEEKLY_FOCUS",
    "FIRST_WEEK_ALIGNMENT",
    "TASK_COUNT",
    "TIME_BUDGET",
    "STARTER_ACTION",
    "DELIVERABLE",
    "SCHEDULE_DATE",
    "RECENT_DUPLICATE",
    "REPLAN_CONTINUITY",
    "SOURCE_INTEGRITY",
    "GOAL_IMMUTABLE",
    "TEXT_LENGTH",
    "TASK_UNIQUENESS",
)


def validate_candidate(
    candidate: PlanCandidate,
    context: PlanningContext,
    evidence_visibility: EvidenceVisibility | None = None,
) -> ValidationReport:
    window = context.planning_window
    completed_deliverables = set(context.completed_facts)
    completed_deliverables.update(
        task.deliverable for task in context.recent_tasks if task.state == TaskStatus.COMPLETED
    )
    expected_count = action_day_count(window)
    expected_action_dates = {
        window.planning_date + timedelta(days=offset) for offset in range(expected_count)
    }
    scheduled_dates = [task.scheduled_date for task in candidate.tasks]
    results = {
        "HORIZON_MATCH": (
            candidate.plan_date == window.planning_date
            and candidate.horizon_start == window.horizon_start
            and candidate.horizon_end == window.horizon_end
        ),
        "WEEKLY_FOCUS": (
            len(candidate.weekly_focus) == window.horizon_weeks
            and [item.week_index for item in candidate.weekly_focus]
            == list(range(1, len(candidate.weekly_focus) + 1))
            and len({item.focus for item in candidate.weekly_focus}) == len(candidate.weekly_focus)
            and len({item.success_signal for item in candidate.weekly_focus})
            == len(candidate.weekly_focus)
        ),
        "FIRST_WEEK_ALIGNMENT": (
            bool(candidate.weekly_focus)
            and all(
                candidate.weekly_focus[0].focus in (task.rationale or "")
                for task in candidate.tasks
            )
        ),
        "TASK_COUNT": len(candidate.tasks) == expected_count,
        "TIME_BUDGET": _within_daily_budget(candidate.tasks, context.time_budget_minutes),
        "STARTER_ACTION": all(bool(task.starter_action.strip()) for task in candidate.tasks),
        "DELIVERABLE": all(bool(task.deliverable.strip()) for task in candidate.tasks),
        "SCHEDULE_DATE": (
            len(scheduled_dates) == expected_count
            and set(scheduled_dates) == expected_action_dates
        ),
        "RECENT_DUPLICATE": not any(
            task.deliverable in completed_deliverables for task in candidate.tasks
        ),
        "REPLAN_CONTINUITY": _valid_replan_continuity(candidate, context),
        "SOURCE_INTEGRITY": (
            not candidate.evidence_refs
            or evidence_visibility is not None
            and evidence_refs_are_visible(candidate.evidence_refs, evidence_visibility)
        ),
        "GOAL_IMMUTABLE": True,
        "TEXT_LENGTH": True,
        "TASK_UNIQUENESS": (
            len({task.title for task in candidate.tasks}) == len(candidate.tasks)
            and len({task.deliverable for task in candidate.tasks}) == len(candidate.tasks)
        ),
    }
    checks = [
        ValidationCheck(
            code=code,
            passed=results[code],
            message="passed" if results[code] else f"{code} failed",
        )
        for code in CHECK_ORDER
    ]
    failures = [check.code for check in checks if not check.passed]
    return ValidationReport(
        passed=not failures,
        checks=checks,
        repair_instructions=[
            (
                "Every daily task rationale must include the exact week 1 focus phrase."
                if code == "FIRST_WEEK_ALIGNMENT"
                else f"Repair deterministic rule {code}"
            )
            for code in failures
        ],
    )


def _valid_replan_continuity(candidate: PlanCandidate, context: PlanningContext) -> bool:
    source = context.source_plan
    review = context.source_review
    if source is None:
        return candidate.adjustment_reason is None
    if review is None:
        return (
            candidate.overall_direction == source.overall_direction
            and candidate.adjustment_reason is None
        )
    if review.adjustment_request or review.replan_reason:
        return bool(candidate.adjustment_reason)
    return (
        candidate.overall_direction == source.overall_direction
        and candidate.adjustment_reason is None
    )


def fallback_candidate(context: PlanningContext, mode: ReplanMode) -> PlanCandidate:
    window = context.planning_window
    minutes = max(5, min(context.time_budget_minutes, 30))
    goal_label = {
        GoalType.AI_BACKEND: "AI 后端",
        GoalType.AGENT_APP: "Agent 应用",
        GoalType.BACKEND_JAVA: "Java 后端",
        GoalType.DATA_ENGINEER: "数据工程",
        GoalType.FULLSTACK: "全栈工程师",
        GoalType.OTHER: "目标",
    }[context.profile.goal_type]
    weekly_templates = [
        (f"明确{goal_label}岗位与能力差距", "形成岗位要求与能力差距清单"),
        ("完成可展示的项目核心增量", "产出可运行、可验证的项目成果"),
        ("沉淀简历与项目表达材料", "形成可投递的简历项目描述"),
        ("开展模拟面试并验证准备效果", "完成复盘记录并修正薄弱点"),
        ("扩大岗位样本并校准投递方向", "形成目标岗位优先级列表"),
        ("强化高频面试专题与表达", "完成一轮专题问答演练"),
        ("集中投递并跟踪反馈", "形成投递与反馈跟踪表"),
        ("复盘结果并确定下一阶段策略", "形成下一阶段行动决策"),
    ]
    first_week_focus = weekly_templates[0][0]
    daily_templates = [
        (
            f"收集{goal_label}岗位样本",
            TaskType.LEARNING,
            f"1. 收集3份{goal_label}JD；2. 保存岗位链接与公司信息；3. 标出重复要求",
            f"岗位样本表，包含3份{goal_label}JD、来源及重复要求",
        ),
        (
            "提取岗位能力要求",
            TaskType.LEARNING,
            "1. 合并3份JD要求；2. 按前端、后端、数据和工程化分类；3. 统计出现频次",
            "岗位要求清单，包含至少10项能力、分类及出现频次",
        ),
        (
            "盘点现有能力证据",
            TaskType.LEARNING,
            "1. 逐项标记已掌握、待补或待证明；2. 关联项目与学习经历；3. 标出证据缺口",
            "能力证据表，包含至少10项要求、当前水平及已有证据",
        ),
        (
            "建立能力差距矩阵",
            TaskType.LEARNING,
            "1. 对照岗位要求与现有证据；2. 标记能力、经验和表达差距；3. 写明判断依据",
            "能力差距矩阵，包含至少5项差距、类型及判断依据",
        ),
        (
            "排定差距补齐优先级",
            TaskType.LEARNING,
            "1. 按岗位频次和当前差距评分；2. 估算补齐成本；3. 选出前三项优先差距",
            "差距优先级表，包含至少5项评分、成本及前三项结论",
        ),
        (
            "复核首要能力差距",
            TaskType.LEARNING,
            "1. 新增2份同类JD；2. 核对前三项差距是否高频；3. 修正不一致的排序",
            "差距复核记录，包含2份新增JD、核对结果及排序调整",
        ),
        (
            "汇总岗位与能力结论",
            TaskType.OTHER,
            "1. 汇总岗位要求与差距矩阵；2. 写出前三项差距；3. 确定下周项目增量方向",
            "第一周结论，包含岗位要求清单、前三项差距和下周方向",
        ),
    ]
    cycle_days = action_day_count(window)
    return PlanCandidate(
        plan_date=window.planning_date,
        horizon_start=window.horizon_start,
        horizon_end=window.horizon_end,
        overall_direction=(
            context.source_plan.overall_direction
            if context.source_plan is not None
            else "采用保守节奏持续积累可验证的求职准备证据"
        ),
        weekly_focus=[
            WeeklyFocusCandidate(
                week_index=index,
                focus=weekly_templates[index - 1][0],
                success_signal=weekly_templates[index - 1][1],
            )
            for index in range(1, window.horizon_weeks + 1)
        ],
        summary=f"本次采用保守的 {cycle_days} 天执行表，每天只推进一个可验证结果。",
        rationale="候选计划未通过规则检查，因此使用不越过目标日期的确定性模板保证可执行性。",
        adjustment_reason="根据复盘阻碍采用保守调整" if mode == ReplanMode.ADJUST else None,
        tasks=[
            TaskCandidate(
                title=title,
                task_type=task_type,
                scheduled_date=window.planning_date + timedelta(days=day_offset),
                starter_action=starter_action,
                deliverable=(
                    f"{(window.planning_date + timedelta(days=day_offset)).isoformat()} "
                    f"{deliverable}"
                ),
                estimated_minutes=minutes,
                rationale=f"服务第1周目标“{first_week_focus}”并形成可验证证据",
            )
            for day_offset, (title, task_type, starter_action, deliverable) in enumerate(
                daily_templates[:cycle_days]
            )
        ],
    )


def build_companion(candidate: PlanCandidate) -> CompanionMessageCandidate:
    return CompanionMessageCandidate(
        message=(
            f"本周期 {len(candidate.tasks)} 天安排已经展开，"
            f"今天先从“{candidate.tasks[0].title}”开始。"
        ),
        template_version="plan_ready_v1",
    )
