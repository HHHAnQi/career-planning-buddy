"""Test-controlled PlanningProvider for deterministic path triggering.

Each instance is programmed with a script: a list of instructions that
specify what each provider method call should return or raise, by call
sequence number. This eliminates reliance on message markers — the test
EXPLICITLY controls which path fires.

Usage:
    provider = ScriptedProvider([
        ScriptedProvider.plan(valid_candidate),       # call 1: planning
        ScriptedProvider.format_repair(valid_candidate),  # call 2: format repair
        ScriptedProvider.raise_error(ConnectionError),    # call 3: raises
    ])
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.schemas.agent_runs import (
    PlanCandidate,
    PlanningContext,
    ProviderUsage,
)


def _usage(tin: int = 200, tout: int = 350, ms: int = 500) -> dict[str, Any]:
    return ProviderUsage(
        model_id="mock-career-planner-v1",
        provider="mock",
        tokens_in=tin,
        tokens_out=tout,
        latency_ms=ms,
    ).model_dump(mode="json")


def default_candidate(planning_date: date | None = None) -> PlanCandidate:
    """A rule-compliant 7-day plan aligned to the CURRENT planning window."""
    from app.core.time import product_today
    if planning_date is None:
        planning_date = product_today()
    from app.schemas.agent_runs import TaskCandidate, WeeklyFocusCandidate
    from app.schemas.enums import TaskType

    pd = planning_date or date(2026, 9, 1)
    horizon_start = pd
    horizon_end = pd + timedelta(days=27)
    return PlanCandidate(
        plan_date=pd,
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        overall_direction="Agent 项目求职准备",
        weekly_focus=[
            WeeklyFocusCandidate(
                week_index=i + 1,
                focus=f"第 {i + 1} 周 Agent 项目重点",
                success_signal=f"第 {i + 1} 周可验证产物",
            )
            for i in range(4)
        ],
        summary="本周完成 Agent 项目闭环",
        rationale="服务第一周重点并形成可验证产物",
        tasks=[
            TaskCandidate(
                title=f"第 {offset + 1} 天实现一个闭环",
                task_type=TaskType.PROJECT,
                scheduled_date=pd + timedelta(days=offset),
                starter_action="1. 打开项目 2. 运行测试 3. 修复失败用例",
                deliverable=f"第 {offset + 1} 天通过的测试报告",
                estimated_minutes=60,
                rationale="服务第 1 周 Agent 项目重点并形成第 1 周可验证产物",
            )
            for offset in range(7)
        ],
    )


def invalid_json_candidate() -> str:
    """Unparseable output (triggers format repair)."""
    return '{"candidate": {"plan_date": "not-a-date"'


def business_violating_candidate(planning_date: date | None = None) -> PlanCandidate:
    """A candidate that parses but violates business rules (budget overrun)."""

    pd = planning_date or date(2026, 9, 1)
    base = default_candidate(pd)
    # Violate TIME_BUDGET: set estimated_minutes way over the 90-min budget
    return base.model_copy(
        update={
            "tasks": [
                task.model_copy(update={"estimated_minutes": 300})
                for task in base.tasks
            ]
        }
    )


class ScriptedProvider:
    """Provider whose responses are pre-programmed by call sequence."""

    def __init__(self, plan_responses: list[Any] | None = None,
                 format_repair_responses: list[Any] | None = None,
                 business_repair_responses: list[Any] | None = None) -> None:
        self._plan = list(plan_responses or [])
        self._format = list(format_repair_responses or [])
        self._business = list(business_repair_responses or [])
        self.plan_calls = 0
        self.format_repair_calls = 0
        self.business_repair_calls = 0
        self.all_calls: list[dict[str, Any]] = []
        # Per-request records (compatible with the real provider's
        # request_records that NodeRunner reads for usage preservation).
        self.request_records: list[dict[str, Any]] = []
        self.sent_request_count = 0

    # --- Helpers for constructing script entries ---
    @staticmethod
    def valid(context: PlanningContext | None = None) -> dict[str, Any]:
        """A valid, rule-compliant plan response."""
        return {
            "candidate": default_candidate().model_dump(mode="json"),
            "usage": _usage(200, 350),
        }

    @staticmethod
    def invalid_json() -> dict[str, Any]:
        """Unparseable JSON (triggers format repair)."""
        return {
            "_raw_text": invalid_json_candidate(),
            "usage": _usage(150, 50),
        }

    @staticmethod
    def business_violating() -> dict[str, Any]:
        """Parses but violates TIME_BUDIT (triggers business repair)."""
        return {
            "candidate": business_violating_candidate().model_dump(mode="json"),
            "usage": _usage(250, 400),
        }

    @staticmethod
    def raise_error(exc: Exception) -> Exception:
        """Marker: this call should raise the given exception."""
        return exc

    @staticmethod
    def repaired_valid() -> dict[str, Any]:
        """Format repair returns a valid plan."""
        return {
            "candidate": default_candidate().model_dump(mode="json"),
            "usage": _usage(180, 300),
        }

    @staticmethod
    def repaired_invalid_json() -> dict[str, Any]:
        """Format repair returns unparseable output again."""
        return {
            "_raw_text": '{"broken": true',
            "usage": _usage(100, 20),
        }

    @staticmethod
    def business_repaired_valid() -> dict[str, Any]:
        """Business repair returns a rule-compliant plan."""
        return {
            "candidate": default_candidate().model_dump(mode="json"),
            "usage": _usage(250, 400),
        }

    @staticmethod
    def business_repaired_still_violating() -> dict[str, Any]:
        """Business repair returns a plan that still violates rules."""
        return {
            "candidate": business_violating_candidate().model_dump(mode="json"),
            "usage": _usage(250, 400),
        }

    # --- PlanningProvider protocol ---
    async def generate_plan(self, *args, **kwargs) -> Any:
        self.plan_calls += 1
        self.all_calls.append({"method": "generate_plan", "call": self.plan_calls})
        result = await self._dispatch_with_record(
            self._plan, self.plan_calls, "generate_plan", kwargs
        )
        return result

    async def generate_agent_turn(self, *args, **kwargs) -> Any:
        return await self.generate_plan(*args, **kwargs)

    async def repair_format(self, *args, **kwargs) -> Any:
        self.format_repair_calls += 1
        self.all_calls.append(
            {"method": "repair_format", "call": self.format_repair_calls}
        )
        return await self._dispatch_with_record(
            self._format, self.format_repair_calls, "repair_format", kwargs
        )

    async def repair_business_rules(self, *args, **kwargs) -> Any:
        self.business_repair_calls += 1
        self.all_calls.append(
            {"method": "repair_business_rules", "call": self.business_repair_calls}
        )
        return await self._dispatch_with_record(
            self._business, self.business_repair_calls, "repair_business_rules", kwargs
        )

    async def _dispatch_with_record(
        self, script: list[Any], call_num: int, method: str,
        kwargs: dict | None = None,
    ) -> Any:
        """Dispatch and record the request (for usage preservation)."""
        self.sent_request_count += 1
        result = self._dispatch(script, call_num, method, kwargs)
        # Extract usage if available
        tokens_in = 0
        if isinstance(result, dict):
            usage = result.get("usage", {})
            tokens_in = usage.get("tokens_in", 0) if isinstance(usage, dict) else 0
        self.request_records.append({
            "operation": method,
            "estimate_total_tokens": tokens_in,
            "sent": True,
        })
        return result

    def _dispatch(
        self, script: list[Any], call_num: int, method: str,
        kwargs: dict | None = None,
    ) -> Any:
        if call_num <= len(script):
            entry = script[call_num - 1]
            if isinstance(entry, Exception):
                raise entry
            if callable(entry):
                return entry(kwargs or {})
            return entry
        # Script exhausted: FAIL LOUDLY — an unexpected provider call
        # must never silently return a default valid response.
        raise RuntimeError(
            f"ScriptedProvider: unexpected {method} call #{call_num}; "
            f"script has {len(script)} entries. This means the graph "
            f"called {method} more times than the test programmed."
        )

    # Stub other methods the executor might call
    async def aclose(self) -> None:
        pass

    @staticmethod
    def context_aware_valid(kwargs: dict) -> dict[str, Any]:
        """Callable script entry: computes a valid plan from the ACTUAL
        context passed to generate_plan, ensuring HORIZON_MATCH and
        WEEKLY_FOCUS always pass."""
        context = kwargs.get("context")
        if context is None:
            return ScriptedProvider.valid()
        window = context.planning_window
        weeks = window.horizon_weeks
        focus = "第 1 周 Agent 项目重点"

        from app.schemas.agent_runs import TaskCandidate, WeeklyFocusCandidate
        from app.schemas.enums import TaskType

        return {
            "candidate": PlanCandidate(
                plan_date=window.planning_date,
                horizon_start=window.horizon_start,
                horizon_end=window.horizon_end,
                overall_direction="Agent 项目求职准备",
                weekly_focus=[
                    WeeklyFocusCandidate(
                        week_index=i + 1,
                        focus=focus if i == 0 else f"第 {i + 1} 周 Agent 项目重点",
                        success_signal=f"第 {i + 1} 周可验证产物",
                    )
                    for i in range(weeks)
                ],
                summary="本周完成 Agent 项目闭环",
                rationale=f"服务{focus}并形成第 1 周可验证产物",
                tasks=[
                    TaskCandidate(
                        title=f"第 {offset + 1} 天实现一个闭环",
                        task_type=TaskType.PROJECT,
                        scheduled_date=window.planning_date + timedelta(days=offset),
                        starter_action="1. 打开项目 2. 运行测试 3. 修复失败用例",
                        deliverable=f"第 {offset + 1} 天通过的测试报告",
                        estimated_minutes=60,
                        rationale=f"服务{focus}并形成第 1 周可验证产物",
                    )
                    for offset in range(7)
                ],
            ).model_dump(mode="json"),
            "usage": _usage(200, 350),
        }

    def __getattr__(self, name):
        # Delegate unknown attributes to avoid breaking the executor
        raise AttributeError(f"ScriptedProvider has no attribute {name}")
