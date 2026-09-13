# 需求规格说明书 SRS v2.0

## 1. 角色

| 角色 | 权限 |
|---|---|
| Guest User | 管理自己的画像、计划、任务、复盘和记忆 |
| Developer | 在 User 权限基础上查看 Trace、Replay 和 Eval |
| External Provider | LLM/Search/Embedding，只通过后端适配器访问 |

## 2. 功能需求

### FR-01 Guest 身份

- 系统应支持无注册 Guest 登录；
- 相同 device_id 可复用用户；
- 服务端不得保存原始 device_id；
- 用户数据必须按 JWT user_id 隔离。

### FR-02 用户画像

- 用户可创建、读取和部分更新画像；
- 核心字段不完整时不能生成正式计划；
- 并发更新使用 version 乐观锁。

### FR-03 Agent Run

- 用户可创建规划或重规划 Run；
- 同用户同时最多一个活动 Run；
- 创建接口幂等；
- 用户可取消；
- Run 必须进入明确终态且只有一个 terminal event；
- completed/degraded 必须返回明确 result_kind；
- 创建时冻结 config snapshot，context_builder 后冻结 input snapshot；
- 事件先持久化再通过 SSE 推送，heartbeat 除外；
- 支持 Last-Event-ID 回放。

### FR-04 计划生成

- 计划包含 1~8 周方向、weekly_focus、summary、rationale，以及从当天开始连续 7 天、每天 1 个任务；
- 任务总时长不超过时间预算；
- 动态来源必须可追踪；
- 模型结构化格式失败最多修复一次；
- 规则失败使用关闭 Tool 的专用 repair 一次，仍失败走模板降级；
- Agent Tool 只读、白名单、最多 2 轮/4 次。

### FR-05 任务执行

- 用户可开始、完成和放弃任务；
- 非法状态转移返回 409；
- 首个任务开始时计划进入 active；
- 完成所有任务后计划进入 completed；
- 过期由系统 Job 设置。

### FR-06 复盘与重规划

- 用户可提交每日复盘；
- 完成/放弃统计由服务端计算；
- 系统可建议重规划；
- 用户确认后才创建 next plan Run；无明显偏差走 continue，有调整需求走 adjust；
- 只有归档来源计划与创建新计划的同一事务成功提交后，来源计划才归档。

### FR-07 记忆

- 用户可查看、关闭、恢复和删除记忆；
- 敏感或不确定记忆先进入候选池；
- 用户确认后才激活；
- 高风险输入不得写记忆。

### FR-08 Trace 与 Eval

- Developer 可查看 Run/Step/Tool/Event；
- Trace 应包含 graph/config/input snapshot、模型、Prompt 版本、Token、耗时、错误；
- 不得暴露 API Key 和未脱敏敏感内容；
- 系统应支持固定 JSONL Eval Case；
- Replay 默认使用原 input/config snapshot 和 Tool fixture。

### FR-09 风险分流

- 明确高风险输入停止普通规划；
- 使用人工审核的固定安全响应配置；
- 不调用自由生成的规划 Agent；
- 不写长期记忆。

## 3. 非功能需求

| ID | 要求 |
|---|---|
| NFR-01 | Python 3.12、FastAPI、SQLAlchemy Async |
| NFR-02 | PostgreSQL 是业务和事件事实源 |
| NFR-03 | MVP 单 Worker 部署，限制必须公开 |
| NFR-04 | 请求、日志和 Trace 具备 request_id/run_id |
| NFR-05 | 外部 Provider 超时和错误统一映射 |
| NFR-06 | Docker Compose 可启动完整演示环境 |
| NFR-07 | 核心状态机和权限路径有自动测试 |
| NFR-08 | 运行时模型通过配置切换，不与编码助手绑定 |

## 4. 验收追踪

| 需求 | API | 表/模块 | 核心测试 |
|---|---|---|---|
| FR-01 | auth/me | users | JWT 隔离 |
| FR-02 | profile | user_profiles | version conflict |
| FR-03 | agent-runs/events | agent_runs/events | 幂等、续传、取消 |
| FR-04 | agent-runs/plans | graph/plans/tasks | Mock/真实结构化输出 |
| FR-05 | tasks | tasks/plans | 状态机 |
| FR-06 | reviews | reviews/agent_runs | replan 事务 |
| FR-07 | memories | memories/candidates | 确认事务 |
| FR-08 | dev | steps/tool/events/evals | Trace 脱敏、Eval |
| FR-09 | agent-runs | risk nodes | 安全测试集 |
