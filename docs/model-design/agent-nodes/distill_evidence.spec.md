# distill_evidence — 成功 Run 后的证据整理

Stage 6B 增强能力，不在计划生成主 Graph 的阻塞路径中。它由
`ExperienceAtomService` 在计划已成功持久化后 best-effort 执行，把本 Run 的
SearchSource 整理成待审核的经验原子候选。

## Input

run_id、goal_type、1~10 个已持久化 SearchSource。

## Output

最多 5 个 `EvidenceAtomCandidate`：title、content、evidence、goal_type、reliability、search_source_ids。

## 约束

- URL/search_source_id 必须来自输入来源；
- 冲突来源同时保留并标注；
- 不直接写正式 experience_atoms，交给 `ExperienceAtomService` 的开发者审核用例幂等持久化；
- 失败不改变已完成 Run 和 Plan；
- 来源质量不足时返回空列表，不编造；
- MVP 单 Worker 下可同步 best-effort 或由管理命令触发，不声称具备可靠后台队列。
