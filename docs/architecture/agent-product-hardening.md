# Agent Product Hardening：产品与工程验收基线

## 1. 单一目标

把“求职搭子”建设为求职准备场景下可演示、可追踪、可评测的 AI Agent 产品：

```text
材料输入 → 上下文选择 → Agent 决策与工具调用 → 用户确认 →
不可变版本/任务落地 → 面试与复盘 → 记忆沉淀 → Eval 验证
```

普通用户页面表达求职价值和下一步行动；开发者页面表达 Runtime、Tool、Memory、Trace、Failure Recovery 与 Eval。不得在普通用户页面使用面试答辩语言，也不得用静态卡片冒充运行证据。

## 2. 页面职责

| 路由 | 用户问题 | 页面职责 |
|---|---|---|
| `/workspace` | 我现在处于什么状态，下一步做什么？ | 全局状态、搭子推荐、推荐依据、闭环进度 |
| `/today` | 我今天具体完成什么？ | 当天任务执行、完成反馈、每日复盘入口 |
| `/materials` | 我的简历和目标岗位如何变得更匹配？ | 冻结材料、证据核验、改写确认、版本链 |
| `/journey` | 整个准备周期如何安排？ | 周期方向、周重点、固定任务安排 |
| `/interviews` | 如何训练和验证表达？ | 面试设置、追问、报告和证据采集 |
| `/reviews` | 哪些进展应改变后续安排？ | 日/周复盘、路线调整入口 |
| `/dev/architecture` | 这是怎样的 Agent 系统？ | 能力地图、边界、工程证据入口 |
| `/dev/runs` | Agent 为什么这样做？ | 快照、节点、Tool、事件、失败与终态 |
| `/dev/evals` | 如何证明改动没有让 Agent 退化？ | Dataset、Trial、Grade、Report、Calibration |

## 3. Human-in-the-loop 简历优化

`suggested_rewrite` 是候选建议，不是最终简历事实。状态机为：

```text
suggested → accepted → applied
         ↘ rejected
```

- `accepted`：用户可编辑建议后锁定；
- `rejected`：记录不采用，不修改简历；
- `applied`：基于被评估的冻结 ResumeVersion 创建子版本；
- 永远不原地覆盖原简历；
- 决策记录保存 assessment、claim、原建议、用户稿、结果版本与时间；
- 证据不足只表达“当前证据不足”，不得升级为事实错误。

## 4. 工程能力与证据

| 能力 | 实现事实 | 可见证据 |
|---|---|---|
| Agent 决策 | 单 Agent + 受控节点 + rule validator/finalizer | AgentStep 时间线、Graph/Config Snapshot |
| Tool Use | 显式白名单、Pydantic 校验、预算、超时、复用 | ToolCall、args hash、provider、error code |
| Context/Memory/RAG | 场景化选择并冻结输入快照 | input snapshot、evidence ref、SHA-256 |
| Human-in-the-loop | 高影响候选由用户确认 | Goal Brief、Rewrite Decision、版本父子关系 |
| Failure Recovery | cancellation/deadline/lease/fallback/唯一终态 | error/fallback、events、terminal invariant |
| Eval | 固定数据集和规则/成对评测 | Experiment、Trial、Grade、Report、Calibration |

## 5. 明确边界

- 当前是单 Agent 受控工作流，不宣称多 Agent、MCP 工具市场或微服务；
- Tool 对业务状态只读，业务写入由 Service 完成；
- `/dev/runs/{id}/replay` 对材料优化使用冻结 input/runtime bundle、Tool/Provider fixture
  重新执行并生成语义 diff；其他 Run kind 不在该 Replay 边界；
- 只有 Tool 和 Provider fixture 都完整时才标记 deterministic；live Provider 重放必须标记
  non-deterministic；
- 简历核验不是背景调查；面试回答只是当前证据来源之一；
- Eval 结果必须标注 provider mode、数据集版本和运行时身份，不能把 mock 指标描述为线上质量。

## 6. 最终演示路径

1. 在材料页保存简历和 JD；
2. 基于冻结材料开始结构化面试，展示问题来源与追问；
3. 生成报告并发起简历主张核验；
4. 回到材料页，编辑并接受一条 `suggested_rewrite`；
5. 应用后展示原版本仍存在、新版本引用父版本；
6. 在工作台看到新的下一步建议和闭环状态；
7. 使用开发者账号打开 Run Trace，展示节点、快照、Tool、事件、错误/降级与唯一终态；
8. 打开 Eval 页面，展示固定 Case、硬门指标、失败样例和校准边界。

## 7. 完成定义

- 前端生产构建和相关组件测试通过；
- Alembic 可从上一 head 升级并通过 schema audit；
- 后端 Schema、Service、API 测试覆盖接受、拒绝、非法应用、幂等应用、用户隔离和原版本不变；
- Docker Compose 可启动，`/health/ready` 正常；
- 浏览器强刷后 `/workspace`、`/materials`、`/dev/architecture`、`/dev/runs`、`/dev/evals` 可访问；
- README/演示说明不把未完成的 V2 Replay、线上 Provider 或生产部署能力写成已交付事实。
