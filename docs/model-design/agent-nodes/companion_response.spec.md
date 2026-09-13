# companion_response — 陪伴话术

## 定位

MVP 中是确定性模板节点，不计入 LLM 预算。它根据已验证计划或 fallback 结果生成简短、具体、不施压的用户提示。

## trigger_tag

- plan_ready
- review_saved
- replan_suggested

任务开始、完成、放弃等实时话术由 Task/Review Service 使用同一模板库生成，不进入计划 Agent Graph。

## Output

```python
class CompanionMessageCandidate(BaseModel):
    trigger_tag: str
    message: str = Field(min_length=1, max_length=500)
    template_version: str
```

## 规则

- 引用具体任务或调整，不泛泛夸奖；
- 放弃时不评判、不制造羞耻感；
- 连续完成不自动加量；
- 情绪低落时共情但不诊断；
- high risk 不使用本节点，直接走 safe_response；
- 模板缺失属于配置错误，计划仍可持久化，但 Run 标记 degraded 并使用通用兜底模板。
