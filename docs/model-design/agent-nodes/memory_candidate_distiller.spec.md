# memory_candidate_distiller — Review 记忆候选提炼

## 定位

Stage 6A 的确定性 Service helper。它只把 Review 和权威 Task 计数转换为 0～2 条
待用户确认的 `MemoryCandidate`，不调用 LLM，不属于 Agent Graph，也不处理
SearchSource/ExperienceAtom。

## 输入

```python
class MemoryDistillationInput(StrictModel):
    user_id: UUID
    source_run_id: UUID | None
    review_id: UUID
    adjustment_request: str | None
    blockers: str | None
    free_text: str | None
    completed_count: int
    abandoned_count: int
    recent_blocker: str | None
```

## 输出

```python
class ProposedMemoryCandidate(StrictModel):
    memory_type: Literal["stable_preference", "execution_pattern"]
    summary: str  # 1..120
    content: dict[str, object]
    sensitivity: Literal["sensitive"]
```

## 确定性规则

- 明确 `adjustment_request` 产生一条 `stable_preference`；
- 当前 blocker 与上一条 blocker 规范化后相同，或当天 abandoned_count ≥ 2，产生
  一条 `execution_pattern`；
- 普通成功 Review 不产生候选；
- 每个 Review 最多 2 条，规则版本固定为 `review_memory_v1`；
- 医疗、身份、联系方式等标记命中时，不从该文本产生候选；不复制完整 free_text。

## 事务、幂等与失败

`ReviewService.create()` 在创建 Review 和 companion message 后、提交事务前调用本
helper。候选初始均为 pending/sensitive，14 天后过期；确认前不创建 Memory。

同一 Review、memory_type 和规范化 summary 在当前用户范围内只写一次。
候选写入使用嵌套事务；提炼或写入失败时回滚候选 savepoint，只记录 review_id、
user_id 等结构化标识，不记录完整 Review，且 Review 主事务仍可成功。

## 测试

- adjustment、重复 blocker、abandoned_count 与无信号 Review；
- 最多 2 条、pending、sensitive、14 天过期；
- Review 幂等不重复写；
- 失败不回滚 Review；
- confirm/reject、Embedding 与用户隔离沿用 Memories Service 契约测试。
