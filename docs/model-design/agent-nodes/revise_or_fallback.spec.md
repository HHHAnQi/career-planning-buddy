# revise_or_fallback — 一次受控修复或模板降级

## 决策

```text
validation pass → 不进入本节点
validation fail + repair_count=0 → 专用修复调用 → rule_validator
修复输出无效/第二次仍 fail → 确定性模板 fallback
```

本节点不会重新进入 `career_planning_agent` 的 Tool Calling 循环，避免重复搜索、重复成本和不可控循环。

## Repair Input

- 原 `PlanCandidate`；
- `ValidationReport.repair_instructions`；
- 最小必要 PlanningContext：goal_type、planning window、time budget、completed facts、blockers、replan mode；
- Tool 关闭；
- 专用 `repair_vN` Prompt。

## Output

```python
class ReviseDecision(BaseModel):
    action: Literal["repaired", "fallback"]
    candidate: PlanCandidate
    fallback_reason: str | None
    repair_count: int
```

## 修复约束

- 只允许 1 次模型调用；
- 调用前按单次输入/输出上限预留总 Token；预留不足时不调用 Provider，直接进入确定性 fallback；
- 不允许新增 evidence_ref；
- 不允许改变 goal_type、planning window、source plan 和 completed facts；
- Provider/Schema 错误直接进入 fallback；
- 修复后必须重新执行完整 `rule_validator`。

## 模板 fallback

模板由程序根据 goal_type、planning window、time budget 和当前日期生成“保守方向 +
完整周重点 + 当前固定周期行动表”：

- plan_date/horizon 必须来自 PlanningWindow；
- 从 plan_date 起生成最多 7 个任务，每天 1 个；最终周期不得越过 horizon_end，并且每项任务只推进其日期所属周重点；
- 时长不超过预算且至少 15 分钟；
- 有明确 starter_action 和 deliverable；
- 不引用外部来源；
- replan 时明确说明“本次采用保守调整”；
- Run 最终状态为 degraded，result_kind=plan，fallback_reason 使用稳定错误码。

不得无限循环，不得把校验失败的原候选直接持久化。
