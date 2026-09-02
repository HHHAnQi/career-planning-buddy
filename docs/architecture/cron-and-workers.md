# Agent Run Lease Worker 与定时任务设计

## 1. Agent Run 执行器

`agent_runs` 是可靠调度事实源。`AgentRunExecutor` 的进程内 Task Registry 只用于取消和
优雅停机，不承担队列语义。多个 Agent Worker 可以竞争 pending Run，但同一 Run 只能由
一个有效 lease 持有者执行。

创建流程：

1. Service 校验幂等和活动 Run；
2. 写 `agent_runs(pending + config snapshot)`；
3. 提交事务；
4. Executor 唤醒 dispatcher；
5. dispatcher 使用 `FOR UPDATE SKIP LOCKED` 抢占 pending Run，写入
   `worker_id/lease_expires_at/heartbeat_at/attempt_count`；
6. Task 使用独立 Session、CancellationToken 和 Deadline 执行 Graph，并定期续租；
7. NodeRunner/Finalizer 在每次持久化前校验 `worker_id + attempt_count + lease`；
8. 正常计划由 Finalizer.finalize_plan 事务收敛；异常/取消由同一 Finalizer 收敛；
9. 终态事务清空 lease，finally 删除本地 Registry 项。

取消流程：

1. 校验 Run 属于当前用户且非终态；
2. 条件更新 `cancel_requested_at`；
3. 找到本进程 Task 时立即调用 `cancel()`；持有 lease 的其他进程由 heartbeat 读取取消标记并取消 owner Task；
4. NodeRunner 在每个节点边界再次读取 `cancel_requested_at`，Task 在取消检查点停止，不进入下一节点；
5. Finalizer 条件写 cancelled 和唯一 `run.cancelled`；
6. 本地 Task 不存在也不影响传播：数据库取消标记是事实源，lease owner 负责收敛且不能重复写 terminal event。

## 2. 恢复语义

应用启动和 dispatcher 周期检查时：

- pending Run 可被任意 worker 抢占；
- running Run 的 lease 过期后写 `run.requeued` 并回到 pending；
- 回收器在行锁内二次检查 lease，避免把刚完成续租的 Run 误回收；
- `attempt_count` 作为 fencing token，旧 attempt 不得续租、写 Step 或提交终态；
- 优雅停机也释放 lease 并 requeue，不写伪终态；
- 超过 `deadline_at` 写 `AGENT_DEADLINE_EXCEEDED`；
- 达到最大 attempt 写 `AGENT_RETRY_EXHAUSTED`；
- 重试从 Graph 起点开始，已成功 ToolCall 可按 Run+参数复用，LLM 调用可能重复；
- Finalizer 和唯一 terminal event 约束防止重复业务终态。

## 3. 定时任务

单进程 APScheduler 或应用内 scheduler 只做轻量清理：

| Job | 周期 | 行为 |
|---|---|---|
| expire_tasks | 每小时 | scheduled_date 早于今天且仍 pending → expired |
| expire_memory_candidates | 每天 | 过期 pending candidate → expired/rejected |
| archive_old_plans | 每天 | 按产品保留策略归档长期 completed 计划 |
| cleanup_guest_users | 每天 | 无业务数据且超过保留期的 guest 用户清理 |
| detect_stale_runs | 每分钟 | 超 deadline 的 pending/running → failed |

所有 Job 必须幂等，使用数据库条件更新和唯一约束，不能与在线请求产生非法双终态。

## 4. 剩余可靠性边界

Agent Run 已具备数据库 claim、lease、heartbeat、attempt fencing、重试上限、取消传播、
任务接管和唯一终态。
正式宣称完整多副本前仍需：节点级 checkpoint、LLM 副作用审计、故障注入、worker 指标告警，
以及把 Eval/Pairwise 执行器升级到同等级的持久调度。
