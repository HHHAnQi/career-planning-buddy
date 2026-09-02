# Career Planning Buddy · 5 分钟演示脚本

这份脚本用于产品演示和技术讲解。目标不是展示“模型会聊天”，而是在 5 分钟内说明：产品能把材料、规划、面试证据和下一步行动连接起来，工程上也能追踪与评测。

## 演示前准备

推荐使用 `.env.example` 的 Mock Provider，避免现场网络、额度和密钥问题：

```powershell
Copy-Item .env.example .env
docker compose up --build -d
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

准备两份脱敏演示文本：

- 一份不含姓名、电话、邮箱和真实公司内部信息的简历；
- 一份公开招聘页面中的目标 JD 摘要。

如需展示 `/dev/*` 页面，必须提前准备服务端角色为 `dev` 的演示账号。普通用户无法通过 HTTP 接口自行提权。

## 演示节奏

| 时间 | 页面 | 要证明什么 |
|---|---|---|
| 0:00–0:35 | `/login`、`/onboarding` | 身份、用户隔离和最小画像是后续上下文基础 |
| 0:35–1:20 | `/materials` | 简历和 JD 有冻结版本，不直接覆盖原材料 |
| 1:20–2:05 | `/workspace`、`/today`、`/journey` | 目标被确认后才生成路线，并落成今天可执行的任务 |
| 2:05–3:20 | `/interviews/new`、`/interviews/:id` | 面试围绕简历和 JD，逐题回答可恢复、有证据分析 |
| 3:20–4:15 | `/interviews/:id/report`、`/growth` | 报告薄弱点可以进入训练动作并在复测中比较 |
| 4:15–5:00 | `/dev/runs`、`/dev/evals` | Agent 的节点、工具、成本、失败和质量回归可追踪 |

## 1. 开场：一句话定位

建议话术：

> Career Planning Buddy 是面向计算机学生的证据化 AI 求职教练。它不是每轮从头给建议，而是把简历、目标 JD、求职路线、任务执行和模拟面试保存在同一个可追踪闭环里，让面试发现的问题真正变成下一步行动。

紧接着说明技术边界：

> 工程上采用 FastAPI、React、PostgreSQL/pgvector 和受控 LangGraph。模型只在明确节点中生成候选，状态转换、持久化、高影响修改和最终结果都有确定性约束。

## 2. 材料：建立可追溯上下文

在 `/materials` 添加演示简历和目标 JD，展示：

- 每次修改产生 `ResumeVersion`，历史版本保留；
- JD 作为独立目标对象，不与简历文本混存；
- 材料诊断区分支持、部分支持、缺少证据和证据不足；
- Agent 只生成改写建议，用户逐条接受或拒绝；
- 最终确认后创建一个带父版本引用的新版本。

关键话术：

> 这里的重点不是自动“润色”，而是每条建议都能回到 JD 要求、简历主张或面试原回答。Agent 无权静默覆盖用户材料。

## 3. 路线与今日行动：从目标到执行

在 `/workspace` 展示系统根据当前状态推荐的下一步，然后进入 `/today`：

1. 输入求职目标、开始日期和结束日期；
2. 展示 Goal Brief，而不是立即生成路线；
3. 补充或确认目标；
4. 观察 SSE 生成进度；
5. 在 `/journey` 查看中期方向和固定 7 天任务周期；
6. 回到 `/today` 开始任务、更新检查项并完成验证。

要讲清楚：

- 计划严格位于用户选择的日期闭区间；
- 当前执行层每天只有一个关键任务，并带起步动作、交付物和时间预算；
- 任务状态由 Service 状态机控制；
- 复盘和重规划创建新版本，不覆盖已完成事实。

## 4. 定向模拟面试：短 Run、可恢复、可核验

从 `/interviews/new` 选择刚才的简历版本和 JD，开始一场 4–6 题训练。

展示重点：

- Session 冻结输入材料版本；
- 每道题由独立的短 Agent Run 处理，刷新页面后仍可恢复；
- 单题分析引用用户原回答，不虚构没有说过的内容；
- 追问次数受限，失败时可以重试或跳过；
- 文本回答始终可用；单题音频只做 ASR 和客观表达指标，不保存原始媒体，也不推断心理状态。

建议准备一段故意缺少量化结果的回答，方便展示报告如何指出证据不足。

## 5. 报告回流：诊断必须进入下一步

完成面试后打开 `/interviews/:id/report`：

1. 展示优势、薄弱点和引用的 Turn 证据；
2. 选择需要训练的动作；
3. 预览将产生的任务/计划调整；
4. 用户一次确认后才写入执行层；
5. 在 `/growth` 查看训练、复盘和可比场次的改善记录。

如果已经准备了两场可比面试，可展示 Retest Comparison。不可比的题目不会被强行解释为“能力提升”。

## 6. 开发者证据：Trace 与 Eval

使用预先准备的 `dev` 账号进入 `/dev/runs`：

- 查看一次 Run 的输入/配置快照和哈希；
- 展开节点时间线、Tool Call、Token、延迟和错误码；
- 展示 terminal event 唯一且位于事件流最后；
- 展示 fallback 原因和 `result_kind`。

进入 `/dev/evals` 或展示 CLI 输出：

```powershell
cd backend
.\.venv\Scripts\python -m evals.v2 run `
  --dataset runtime-smoke `
  --cases runtime-tool-error-01 `
  --provider-mode mock `
  --trial-count 1
```

关键话术：

> 我没有把一次好看的模型输出当作质量证明。仓库会冻结 Git、Graph、Prompt、Model、Tool、Context 和数据集版本，用确定性规则和 Fixture 做回归。Pairwise 在真实人工校准不足时只标记为诊断结果。

## 7. 结尾：用五个工程点收束

1. 单核心 Agent + 受控节点，避免为了概念堆砌多 Agent；
2. API、状态机和 Pydantic 契约约束模型输出；
3. PostgreSQL 同时承载业务事实、Run 租约、快照和 SSE 事件；
4. 简历改写、长期记忆和训练动作都有人类确认边界；
5. Mock/Fixture、Trace 和 Eval 让行为可以稳定复现和回归。

## 常见追问

| 问题 | 回答要点 |
|---|---|
| 为什么不用多 Agent？ | MVP 只有规划决策需要受控工具调用，其他步骤更适合确定性节点，成本和故障面更小。 |
| 为什么不用 Redis/Celery？ | 当前单机部署中，PostgreSQL lease 已覆盖 Run claim、heartbeat、恢复和取消；Eval 仍明确限制单 Worker。 |
| 如何避免模型乱写数据库？ | Agent 不依赖 ORM，输出先过 Schema 和规则，所有写入由 Service/Repository 事务完成。 |
| 如何处理上下文过长？ | 场景化候选选择、确定性压缩、Token 预算和冻结的 Context Manifest。 |
| 如何证明输出质量？ | 固定数据集、硬规则 Grader、Provider 调用审计、Fixture Replay、Pairwise 与人工校准门禁。 |
| 项目是否生产可用？ | 当前适合本地单机部署；多副本 Eval、集中 Secret、监控和真实用户验证仍是生产部署前需要完成的工作。 |

## 演示禁区

- 不展示真实密钥、`.env`、请求 Header 或 Provider 控制台；
- 不使用真实姓名、联系方式、未公开 JD、面试录音或用户原文；
- 不现场下载本地 Embedding 模型；
- 不把 Mock 结果说成真实 Provider 结果；
- 不把离线 Eval 说成真实用户留存或能力提升；
- 不把 `legacy_trace_clone` 描述成完整 Graph Replay。

## 建议录制素材

建议补充以下脱敏素材，并放在 `docs/assets/screenshots/`：

1. 工作台与下一步建议；
2. 材料诊断和改写确认；
3. 面试房间与逐题分析；
4. 面试报告到训练动作；
5. Run Trace 与 Eval Report；
6. 一段 60–90 秒的主流程 GIF 或视频链接。
