# Developer Trace 页面

## 列表

展示：Run ID、状态、result_kind、意图、graph version、模型、成本、Token、耗时、fallback、时间。

## 详情

典型计划时间轴：

```text
run.created
risk_gate
intent_router
context_builder + input snapshot
career_planning_agent
[tool calls]
rule_validator
[revise_or_fallback → rule_validator]
companion_response
persist
plan.ready
run.completed/degraded
```

其他终止路径：

```text
risk_gate → safe_response → run.degraded
intent_router → clarification → run.degraded
any node → finalizer → run.failed/cancelled
```

每个 Step 展示：状态、attempt、脱敏输入/输出 hash、Prompt 版本、实际模型、Token、耗时、错误。Tool 展示：round、contract version、参数 hash、fixture 是否存在、结果摘要和 latency。

页面还应显示：

- config snapshot；
- input snapshot 的版本/引用摘要；
- Budget 使用量；
- terminal event 是否唯一；
- result_kind/result/final_plan_id 一致性；
- Replay 是否使用 fixture、是否 live、目标 model/prompt。

禁止在开发者页展示 API Key、JWT、完整敏感输入和完整网页正文。
