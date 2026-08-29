# safe_response — 固定安全响应

## 定位

风险分支的终止节点，不调用 LLM。内容来自 `SafetyResourceConfig`，按部署地区维护并定期复核，不在多份 spec 中散落硬编码热线或 URL。

## Input

```python
class SafeResponseInput(BaseModel):
    run_id: UUID
    category: RiskCategory
```

## Output

```python
class SafeResponse(BaseModel):
    message: str
    resources: list[SafetyResource]
    disclaimer: str
```

## 行为

1. 从审核后的配置构建 SafeResponse；
2. 返回 `TerminalBranchResult(result_kind=safe_response, payload=...)`；
3. Executor 调用 `AgentRunFinalizer.finalize_degraded()`，在单事务中写结果和 `run.degraded`；
4. fallback_reason=`high_risk_routed`；
5. 不调用 intent/planning 模型；
6. 不创建计划、任务、SearchSource、Memory 或 MemoryCandidate。

前端刷新后可从 Run 详情重新读取安全响应。Trace 只记录 category 和 resource ids，不保存命中的完整敏感原句。

## 安全要求

- 不提供诊断；
- 不让 LLM 自由生成紧急资源；
- 资源配置需要人工审核；
- 如果部署地区未知，使用通用的“联系当地紧急服务或可信任的人”提示，不猜号码；
- 配置缺失时使用审核过的通用模板，不能回到规划主链。
