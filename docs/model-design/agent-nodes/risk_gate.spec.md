# risk_gate — 风险分流

## 定位

Graph 第一个节点。先做本地规则判断，仅在规则疑似但不确定时调用 `LLM_ROUTER_MODEL` 做结构化分类。它不是医疗诊断，也不把普通求职压力误判成专业诊断结论。

## Input

```python
class RiskInput(BaseModel):
    run_id: UUID
    message: str
```

## Output

```python
class RiskResult(BaseModel):
    level: Literal["none", "high"]
    category: Literal["self_harm", "mental_health", "legal", "financial", "other"] | None
    method: Literal["rule", "classifier", "rule_and_classifier"]
    matched_rule_ids: list[str]
    confidence: float | None
```

## 决策

- 明确高风险本地规则命中：直接 high，不调用分类器；
- 疑似规则命中：允许分类器 1 次；
- 无规则命中：none；
- 分类器 Provider 失败：明确本地高风险仍 high，否则 none 并在 trace 标记 `classifier_unavailable`；
- level=high 直接进入 safe_response，不再执行意图、规划、Tool、记忆。

## 隐私与 Trace

仅记录 level、category、method、matched_rule_ids、confidence、latency_ms。不得把命中的完整敏感原句复制进 trace_data、日志或事件 payload。

## 测试

- 明确 high 不调用分类器；
- 疑似 case 调用分类器；
- 分类器超时的保守策略；
- 普通求职焦虑不误路由；
- high 路径没有 Plan/Tool/Memory 写入。
