# Eval 系统

## Case 格式

```json
{
  "id": "case-001",
  "profile": {
    "goal_type": "agent_app",
    "stage": "preparing",
    "time_budget_minutes": 90,
    "skill_level": "intermediate"
  },
  "message": "帮我制定今天能开始的 Agent 项目计划",
  "source_plan": null,
  "task_history": [],
  "review_history": [],
  "tool_fixtures": {},
  "expected": {
    "intent": "create_plan",
    "result_kind": "plan",
    "allowed_status": ["completed", "degraded"],
    "horizon_weeks": 4,
    "max_tasks": 3,
    "tasks_on_planning_date": true,
    "must_fit_budget": true,
    "expected_tools": []
  }
}
```

高风险、缺槽、replan、Tool 失败等 Case 使用同一结构扩展 expected。

## Grader

1. Intent Grader；
2. Result Kind/Terminal Grader；
3. Schema/Horizon Grader；
4. Time Budget Grader；
5. Startability Grader；
6. Deliverable Grader；
7. Source Integrity Grader；
8. Tool Policy Grader；
9. Replan Continuity Grader；
10. Safety Routing Grader；
11. Replay Grader（仅在 V2 真实重执行与结果 diff 完成后启用）。

规则可判的指标优先使用程序 Grader。LLM Judge 只作为辅助，不作为唯一真值。

## 报告

输出：

- 总通过率与各 Grader 通过率；
- completed/degraded/failed 分布与 fallback 原因；
- 平均 LLM/Tool 次数、Token、成本、延迟；
- Tool 白名单和来源完整率；
- 失败 Case、旧/新 Prompt diff；
- non_deterministic Replay 数量。

CI 默认跑不访问真实网络的 smoke 子集；完整 30 Case 手动或夜间运行。

## 当前能力边界（PR-0）

- `stage5-v1` 是确定性 Mock 数据集，可用于规则回归，不代表真实模型质量；
- `quality_reviewer` 当前只是确定性完整性检查，不是 LLM Judge；
- `snapshot_replay` 已从报告移除，禁止用恒真值伪装 Replay 能力；
- V2 Replay 尚未交付前，报告不得宣称“快照重执行通过”或“Replay 一致性通过”。
