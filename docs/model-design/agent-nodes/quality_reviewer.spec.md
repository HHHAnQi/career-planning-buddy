# quality_reviewer — 可选 LLM Judge

Stage 5 增强能力，不属于 MVP 纵切必经路径，也不替代确定性 `rule_validator`。

## 作用

评价难以完全程序化的维度：计划连续性、理由充分性、语气压力、证据与结论一致性。

## Output

```python
class QualityReview(BaseModel):
    passed: bool
    score: float = Field(ge=0, le=1)
    issues: list[str]
    repair_instructions: list[str]
```

## 运行模式

### 默认：离线 shadow

- 由 Eval/Replay 命令在原 Run 终态后执行；
- 读取原 Run 的 input/config snapshot 与最终计划；
- 结果写入独立 `eval_run_results` 或报告 Artifact，不写原 Run 的 `agent_steps/agent_events`；
- 不改变线上结果，也不破坏“terminal event 是最后事件”的约束；
- 使用独立 Eval 预算，不能混入原 Run 成本统计。

### 实验：online enforce

- 仅在 `QUALITY_REVIEW_ENFORCE=true` 时，于 `companion_response/persist` 前同步执行一次；
- 低于阈值且 `repair_count=0` 时，可把问题合并到唯一一次业务修复；
- 修复后只重新跑确定性 validator，不再次调用 reviewer；
- reviewer 失败或超时：继续使用确定性规则已通过的候选，不把 Judge 故障当业务失败；
- 该调用计入 Run LLM/Token/Deadline 预算，并写正常 Trace。

## 约束

- Judge 不直接修改 Candidate；
- Prompt 与主 Agent 分离版本；
- 不允许调用 Tool；
- 禁止使用同一次 Judge 输出作为唯一 Eval 真值；
- 线上主模型和 Judge 尽量使用独立 Prompt，模型是否相同由配置决定并记录。
