# persist — 受控持久化终态节点

## 定位

`persist` 是唯一的 terminal-aware Graph 节点。它不自行操作 ORM，而是把已校验的计划交给 `AgentRunFinalizer.finalize_plan()`；Finalizer 在同一个数据库事务中调用 `PlanPersistenceService`、完成 persist step、更新 Run 并写终态事件。

只有经过完整 `rule_validator` 的正常候选，或程序生成且再次通过规则校验的模板候选，才能进入本节点。

## Input

run_id、user_id、validated `PlanCandidate`、`CompanionMessageCandidate`、evidence refs、source_plan_id、fallback_reason。

## 单事务动作

1. `SELECT ... FOR UPDATE` 锁定 Run 和当前用户 generated/active 计划；
2. 再次确认 Run 为 running、未请求取消、未超过 Deadline；
3. replan 时在当前事务内把旧 generated/active/completed 来源 Plan 更新为 archived；若后续失败，事务整体回滚，旧计划状态恢复；
4. 创建新 Plan(generated, parent_plan_id=旧计划 id)；
5. 批量创建当前固定周期（1~7 天）的 Tasks；
6. 创建 CompanionMessage；
7. 校验并写入 `plans.evidence_refs_json`，引用本 Run/当前用户可用的 SearchSource、Memory、ExperienceAtom；
8. 把 persist 对应 `agent_steps` 更新为 completed，并写 `node.completed(persist)`；
9. 写 `plan.ready`；
10. 更新 `agent_runs.final_plan_id`、`result_kind=plan`、`result_payload_json`、terminal status 和 finished_at；
11. 写唯一的 `run.completed` 或 `run.degraded` terminal event；
12. 提交事务。

`node.completed(persist)`、`plan.ready` 和 terminal event 都在同一事务中分配连续 sequence；terminal event 必须最后插入。事务提交后 NodeRunner 不得再为 persist 追加事件。

任何一步失败全部回滚，再由 `AgentRunFinalizer.finalize_failed()` 使用新的短事务把 persist step 标记 failed、写 `node.completed(persist,status=failed)`，最后写 `run.failed`。不得出现“Plan 已提交但 Run 仍 running”“Run completed 但没有 Plan”或“terminal event 后还有 node event”。

## 结果语义

- 正常候选：status=completed；
- 模板 fallback：status=degraded，fallback_reason 非空；
- `result_payload_json` 只保存 plan_id、status、plan_date、horizon_end、summary 和 task_count，完整计划通过 `/plans/{id}` 查询。

Memory candidate、离线 quality reviewer 与证据蒸馏不和计划主事务强绑定。它们使用独立记录，失败不回滚 Plan，也不得向已终态 Run 追加 `agent_events`。
