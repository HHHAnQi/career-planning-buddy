"""Prompt for bounded, structured goal extraction before user confirmation."""


def goal_understanding_messages(
    message: str,
    *,
    planning_days: int | None = None,
    daily_budget_minutes: int | None = None,
) -> list[dict[str, str]]:
    constraints = (
        "未提供规划时间约束。"
        if planning_days is None or daily_budget_minutes is None
        else (
            f"用户确认的硬约束：共 {planning_days} 个自然日，"
            f"每天最多 {daily_budget_minutes} 分钟。不得建议延长日期。"
        )
    )
    return [
        {
            "role": "system",
            "content": (
                "你是职业规划产品的目标信息抽取器。只抽取用户明确表达的信息，"
                "不替用户确认，也不输出推理过程。返回严格 JSON，字段必须为 "
                "objective_type、target_role、objective、capability_focus、tech_stack、"
                "duration_weeks、deliverables、success_criteria、feasibility、"
                "feasibility_reason、constrained_strategy。objective_type 只能是 "
                "career_plan、project、application、interview、skill_transition 或 null。"
                "当用户的总体目标是求职准备，即使包含项目和面试子任务，也应选择 "
                "career_plan。objective 是用户本次希望达成的总体目标。未知字符串用 null，"
                "未知列表用 []，duration_weeks 只能是 1 到 8 或 null。"
                "feasibility 只能是 feasible、tight、unrealistic 或 null。"
                "结合给定时间和每日预算判断目标可行性；tight/unrealistic 必须说明具体理由，"
                "并给出在不越过日期前提下，通过缩小范围、调整优先级或降低交付深度仍可执行的方案。"
            ),
        },
        {"role": "user", "content": f"{constraints}\n用户目标：{message}"},
    ]
