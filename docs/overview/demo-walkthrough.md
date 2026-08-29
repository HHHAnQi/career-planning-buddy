# Demo Walkthrough — 项目演示脚本

| 项目 | 内容 |
|---|---|
| 版本 | v1.0 |
| 状态 | 设计基线 |
| 面向对象 | 项目展示者、面试官、评审者 |
| 定位 | 用 3-5 分钟说明 Career Planning Buddy 的用户价值、核心链路、工程亮点和后续优化方向 |

English summary: Demo walkthrough script for Career Planning Buddy. It provides a short product and engineering demo flow for interviews, portfolio review, and project presentations.

---

## 1. 演示目标

本演示不是只展示“AI 能聊天”，而是展示一个工程化 AI 应用：

- 有清晰用户场景；
- 有可执行任务产出；
- 有 Agent 受控工作流；
- 有状态机、Trace、Replay/Eval 的工程闭环；
- 有安全分流和记忆管理；
- 有 Mock 到真实 Provider 的演进路径。

## 2. 3-5 分钟演示结构

| 时间 | 内容 | 目标 |
|---|---|---|
| 0:00-0:30 | 项目定位 | 说明 Career Planning Buddy 是 AI 求职规划搭子，不是普通聊天机器人 |
| 0:30-1:20 | 首次建档 | 展示目标方向、阶段、每日可用时间 |
| 1:20-2:20 | 生成规划 | 展示规划对话、SSE 进度、今日任务 |
| 2:20-3:00 | 任务执行与复盘 | 展示开始/完成/放弃、每日复盘、重规划 |
| 3:00-4:10 | 工程 Trace | 展示 run、节点、tool call、cost、fallback |
| 4:10-5:00 | 总结亮点 | 说明单 Agent、Trace/Eval、状态闭环与安全 |

## 3. 开场话术

```text
Career Planning Buddy 是一个面向计算机学生的 AI 求职规划搭子。
它解决的问题不是“给我一堆泛泛建议”，而是每天根据用户目标、当前阶段、可用时间和执行反馈，生成 1-3 个能马上开始的任务，并通过复盘持续调整。

工程上，它采用 FastAPI + React + PostgreSQL + LangGraph。
Agent 不是随意写库或自由发挥，而是被限制在单核心 Agent + 受控节点 + Trace/Replay/Eval 的框架里。
```

## 4. 演示路径一：首次建档

演示入口：`/` 或 `/chat`

用户输入或填写：

| 字段 | 示例 |
|---|---|
| 目标方向 | AI 应用开发 / Agent 应用 |
| 当前阶段 | 有 Python 基础，有一个项目雏形，准备秋招 |
| 每日可用时间 | 工作日 2 小时，周末半天 |

要讲清楚：

- 没有档案时不直接生成计划；
- 信息不足会先澄清；
- 建档后的信息会进入后续 context_builder；
- 这一步服务于“计划可执行”，不是收集简历表单。

## 5. 演示路径二：生成规划

演示入口：`/chat`

示例请求：

```text
帮我制定 5 周后的 Agent 应用开发秋招计划，今天先给我能开始做的任务。
```

展示重点：

| 画面 | 说明 |
|---|---|
| 对话输入框 | 用户自然语言发起规划 |
| 运行进度 | SSE 展示 `run.created`、`progress`、`plan.ready` |
| 今日任务 | 输出 1-3 个任务 |
| 起步动作 | 每个任务有 starter_action |
| 陪伴话术 | 不是冷冰冰任务列表 |

建议讲法：

```text
这里不是一次 LLM 调用直接返回答案。
后端会创建 agent_run，经过 risk_gate、intent_router、context_builder、career_planning_agent、rule_validator、一次受控修复、companion_response、persist 等节点。quality_reviewer 默认只在 Stage 5 的 Eval/Replay 中离线 shadow 评测，不向线上 Run 追加事件。
前端通过 SSE 展示中间状态，最后拿到可保存、可追踪的计划和任务。
```

## 6. 演示路径三：今日任务执行

演示入口：`/today`

操作：

1. 点击一个任务的“开始”。
2. 展示状态从待开始变为进行中。
3. 点击“完成”或“放弃”。
4. 如果放弃，填写阻碍原因。

要讲清楚：

- 任务状态不是前端随便改，必须符合状态机；
- 完成和放弃都会影响后续复盘；
- 用户每天主要面对的是“今天做什么”，不是复杂项目管理工具。

## 7. 演示路径四：每日复盘与重规划

演示入口：`/reviews/new`

示例复盘：

```text
今天完成了 LangGraph 节点梳理，但没有写测试。主要卡在状态机转移不确定。明天只有 1 小时。
```

展示结果：

| 情况 | 行为 |
|---|---|
| 不需要明显调整 | 保存复盘，用户点击后以 continue 生成明日行动批次 |
| 需要调整 | 生成 suggested_replan，用户确认后创建 replan run |

要讲清楚：

- Career Planning Buddy 的闭环是“规划 → 执行 → 复盘 → 调整”；
- 重规划基于真实执行反馈，不是每天重新拍脑袋；
- 用户确认后才进入重规划。

## 8. 演示路径五：记忆管理

演示入口：`/memories`

展示：

- 已确认记忆；
- 候选记忆；
- 确认、拒绝、关闭、删除。

建议讲法：

```text
长期记忆不是模型偷偷记住。
系统把可复用信息整理为候选，敏感内容需要用户确认。
用户可以查看、关闭或删除记忆。
```

## 9. 演示路径六：开发者 Trace

演示入口：`/dev/traces`

展示：

| 内容 | 说明 |
|---|---|
| run 列表 | 每次规划都有 run |
| step 时间线 | 查看本次实际执行的核心与增强节点 |
| tool calls | 看到 Agent 调用了哪些只读工具 |
| cost/latency | 成本和耗时可观察 |
| fallback_reason | 降级原因显式化 |
| Replay/Eval 入口 | 工程闭环，而不是只看一次输出 |

建议讲法：

```text
这是我认为 Agent 项目最重要的工程化部分。
如果没有 Trace，就很难知道模型为什么给出这个计划；如果没有 Replay/Eval，就很难稳定改 Prompt 和节点逻辑。
```

## 10. 风险分流演示

如果演示时间足够，可以展示安全分流，但不要输入过度刺激或详细危险内容。

讲清楚即可：

- 高风险内容不进入普通规划；
- 不写长期记忆；
- 返回固定支持话术和资源；
- Trace 记录分流结果。

## 11. 工程亮点总结

建议最后用 5 点收束：

1. **单核心 Agent + 受控节点**：避免伪多 Agent 和不可控写入。
2. **契约先行**：API、Schema、状态机、DB、节点 spec 先于实现。
3. **Mock 纵切**：先打通前后端、SSE、DB、Trace，再接真实模型。
4. **Trace/Replay/Eval**：让 Agent 输出可追踪、可复盘、可评测。
5. **安全与记忆控制**：高风险分流，敏感记忆用户确认。

## 12. 面试官可能追问

| 问题 | 回答要点 |
|---|---|
| 为什么不用多 Agent？ | MVP 中只有一个真正需要自主选择工具的规划 Agent，其他都是受控节点；这样更可测、更可控 |
| 为什么先 Mock？ | Mock 验证契约、SSE、DB、状态机和 Trace；真实模型接入后只替换 Provider |
| 如何评估输出质量？ | 5 维质量评分 + LLM Judge + 固定 Eval case + Bad Case 回流 |
| 如何避免模型乱写数据库？ | Agent 只读工具，所有写入经 persist 节点和 Service 事务 |
| 数据从哪里来？ | O*NET/ESCO/岗位样本/手工 experience_atoms，后续用 RAG 接入 |
| 如何控制成本？ | 轮数、工具调用数、Token、cost_cny 都在 budget 和 trace 中记录 |

## 13. 演示前检查清单

| 检查 | 标准 |
|---|---|
| 后端健康 | `/health` 返回 ok |
| 前端可访问 | 首页正常加载 |
| Mock run | 能生成固定任务 |
| SSE | 进度事件可见 |
| 任务操作 | 开始/完成/放弃可用 |
| 复盘 | 能提交并返回结果 |
| Trace | 能看到最近 run |
| 数据 | 使用 mock 或脱敏样例 |

## 14. 不建议演示的内容

- 不现场调真实模型 key。
- 不现场改 Prompt。
- 不展示未脱敏用户原文。
- 不把开发者 Trace 当普通用户功能介绍太久。
- 不演示还没有验收通过的远期能力。

## 15. 关联文档

- [用户使用说明书](./user-manual.md)
- [端到端运行流程](../model-design/end-to-end-runtime-flow.md)
- [前端页面使用流](../model-design/ui-spec/product-navigation.md)
- [阶段化交付定义](../governance/stage-delivery-definition.md)
