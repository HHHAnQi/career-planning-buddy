# Career Planning Buddy 用户视角复审

## 整改结果（2026-08-13）

本报告中的代码侧 P0/P1 问题已完成一轮整改，当前可进入受控真实用户验证：

- 面试创建后立即进入资源页，Session 返回 `active_run`，首题、回答分析、跳过和报告生成均可在刷新后恢复；
- `profile_complete` 与 `planning_window_valid` 已拆分，规划日期过期不再锁住材料、面试和报告；
- 所有业务日历语义统一为 Asia/Shanghai（UTC+8），数据库时间戳继续使用 UTC；
- 首页按未完成面试、未读报告、今日任务和首次面试展示主动作，报告查看状态随当前 Guest 浏览器保存；
- 首次材料准备保留面试意图并可一键返回，已有材料默认选择最新版本；
- 报告证据已映射到题号与问题摘要，补充 strengths、事实判断、缺失要点、建议结构、限制和中文枚举；
- 训练 Replan 与 Retest 都会携带 Run 落点，并统一补充失败提示；
- 历史列表显示岗位、公司、简历版本、日期和中文动作状态；材料支持软删除，非运行中的单场面试支持删除；
- Guest 仅当前浏览器可恢复的限制已在“我的”页面显著说明，并提供清除当前访客全部关联数据的入口；
- `plans.source_run_id` 显式延后创建外键，Alembic 循环依赖警告已消除。

整改后验收：Ruff、Mypy、Alembic upgrade/check、701/701 后端测试、Stage 5 30/30、Stage 6 12/12、34/34 前端测试、生产构建均通过；Interview 确定性 Eval 18/18 通过。

唯一尚未完成的发布门槛：使用真实 Provider 完成 Golden Cases 人工校准，并邀请 5～10 名真实求职用户完成端到端价值验证。当前 Interview Eval 仍明确标记 `diagnostic_only=true`、`human_calibration_required=true`，因此不能只凭 Mock 结果宣称公开 Beta 就绪。

> 审查日期：2026-08-13  
> 审查对象：`feat/ly` 当前工作区（包含尚未提交的 Career Coach Batch 1～3 改动）  
> 审查方式：产品与代码静态审查、契约与状态机核对、确定性测试和 Eval 验证  
> 审查视角：真实求职用户能否顺利完成“材料 → 面试 → 报告 → 训练 → 复测”主链路

## 1. 结论

当前版本已经具备很扎实的工程底座，也形成了清晰的“证据化面试训练”产品方向；但从真实用户角度，仍不建议直接作为公开 Beta 发布。

主要原因不是后端链路不完整，而是前端尚未完整承接持久化状态：当用户在 AI 生成第一题或分析回答期间刷新页面，页面无法可靠恢复对应 Run；规划日期过期还会阻断面试入口；首页也没有按设计优先展示未完成面试或已生成报告。这些问题会使一个后端已经保存成功的操作，在用户眼中表现为卡死、失败或重新开始。

发布判断：

| 维度 | 结论 | 说明 |
|---|---|---|
| 工程确定性基线 | 通过 | 静态检查、迁移、696 个后端测试、33 个前端测试、既有 Eval 和构建全部通过 |
| Interview Mock 质量门槛 | 通过但仅诊断 | 18/18 Case 通过；报告明确为 `diagnostic_only`，仍需人工校准 |
| 核心用户路径 | 暂不通过 | AI 处理中刷新、建档门槛和首页下一步引导仍有阻断性问题 |
| 隐私与长期使用 | 暂不通过 | Guest 身份不可跨设备恢复，材料和面试缺少用户可见的数据删除入口 |
| 公开 Beta 建议 | 暂缓 | 先完成本文 P0，并用真实 Provider 和 5～10 名用户验证 |

## 2. 相比上一轮审查

更新后的代码新增或完善了 Interview Session、Turn、Report、训练动作、Retest、Resume Assessment、Audio、正式 Interview Eval 和相关用户隔离/失败恢复测试。后端的业务状态与持久化能力明显强于上一版。

但上一轮的主要用户体验问题尚未实质解决：

| 上一轮问题 | 当前状态 |
|---|---|
| 回答分析中刷新无法恢复 | 仍存在 |
| 规划日期过期阻断面试 | 仍存在 |
| “今天”使用 UTC 日期 | 仍存在 |
| 首次材料导入与面试设置割裂 | 仍存在 |
| 报告直接显示内部 Turn ID/英文枚举 | 仍存在 |
| 隐私入口与实际数据控制不一致 | 仍存在 |
| 第一题生成中刷新丢失上下文 | 本轮新确认 |
| 首页未按未完成面试/报告优先级引导 | 本轮新确认 |

## 3. P0：公开测试前必须修复

### P0-1 面试 AI 执行过程中刷新，无法从服务端恢复当前 Run

用户影响：高。用户已经提交材料或答案，后端也已经持久化并启动 Run，但刷新后页面可能显示空表单、错误失败提示或没有可操作内容。

存在两个具体场景：

1. 在“正在生成第一题”时刷新：`InterviewSetupPage` 的 `runId` 和新建 `interview_id` 只保存在组件状态及 mutation 结果中。刷新后两者同时丢失，用户回到一张可重新提交的设置表单。
2. 在“正在分析回答”时刷新：Turn 会恢复为 `answer_status=submitted`、`analysis_status=running`，但 `InterviewSessionResponse` 不返回 `analysis_run_id`，房间页也没有该状态的展示分支。`useInterview` 只轮询 `draft` 和 `report_generating`，不会轮询 active/running Turn。

代码证据：

- [`InterviewSetupPage.tsx`](../../frontend/src/pages/InterviewSetupPage.tsx)：新建 Session 和 Run 只进入 `useState`，完成前没有将 Session ID 写入 URL。
- [`InterviewRoomPage.tsx`](../../frontend/src/pages/InterviewRoomPage.tsx)：`waiting` 仅由本地 `runId` 或 `report_generating` 决定；只处理 pending、failed、skipped Turn。
- [`interviews.ts`](../../frontend/src/api/interviews.ts)：Session 查询只对 `draft`、`report_generating` 自动轮询。
- [`interview.py`](../../backend/app/models/interview.py) 已保存 Run 外键，但 [`interviews.py`](../../backend/app/schemas/interviews.py) 中的 `InterviewSessionResponse`/`InterviewTurnResponse` 没有返回当前 Run 引用。
- [`InterviewRoomPage.test.tsx`](../../frontend/src/pages/InterviewRoomPage.test.tsx)：当前只有“首题失败后可重试”测试，没有覆盖分析中刷新恢复。

最小改进：

1. 创建 Interview 成功后立即导航到 `/interviews/{id}`，Run 进度由资源页承接，不在设置页等待终态。
2. Session DTO 返回一个受控的 `active_run` 引用，至少包含 `run_id/status/events_url/run_kind`；或者提供等价的当前操作资源。
3. 房间页从 Session 恢复 SSE/轮询，并明确渲染 `submitted + running`、`skipped + running`、`draft + active_run`。
4. Run 终态后重新 GET Session，Session/Turn 始终作为权威事实源。

验收标准：

- 生成第一题、分析回答、跳过后生成下一题、生成报告四个阶段分别刷新页面，均能恢复正确进度并最终进入下一状态。
- 恢复期间不出现“可能生成失败”的误报，不允许重复提交产生第二个 Session/Turn。
- 对应前端测试覆盖 pending、running、failed、cancelled、completed 五类 Run 结果。

### P0-2 规划周期过期会锁住整个面试产品

用户影响：高。已有简历、面试和报告的用户，只因旧规划的结束日期过去，就会被所有核心路由重定向到 Onboarding，无法继续面试或查看报告。

代码与设计冲突：

- [`auth.py`](../../backend/app/api/auth.py) 把 `deadline >= UTC 今天` 作为 `profile_complete` 条件。
- [`LoginRoute.tsx`](../../frontend/src/pages/LoginRoute.tsx) 和 `RequireProfile` 使用这一字段守卫全部产品页面。
- [`career-coach-v2-design.md`](../v2/career-coach-v2-design.md) 明确规定开始/结束日期不得成为开始第一场面试的额外门槛。

最小改进：

- 将面试所需画像完整度与计划所需画像完整度拆开，例如 `profile_complete` 与 `planning_window_valid`。
- 日期过期只阻止创建新规划，不能阻止材料、面试、报告、成长历史和隐私页面。
- 旧周期过期时在需要规划的操作旁提示更新日期，不进行全局强制跳转。

验收标准：

- 构造 deadline 为昨天、但已有 Resume/Interview 的用户，仍可进入并完成面试、查看报告。
- 该用户尝试创建新 Plan 时收到局部更新日期提示。

### P0-3 “今天”的产品日期与用户本地日期不一致

用户影响：中高。在 Asia/Shanghai 的 00:00～08:00，前端已经显示新的一天，后端仍按 UTC 查询前一天任务；日期有效性、复盘和计划起点也可能产生相同偏差。

代码证据：

- [`TodayPage.tsx`](../../frontend/src/pages/TodayPage.tsx) 使用浏览器本地 `new Date()` 展示日期。
- [`auth.py`](../../backend/app/api/auth.py)、[`plans.py`](../../backend/app/services/plans.py)、[`goal_briefs.py`](../../backend/app/services/goal_briefs.py)、[`reviews.py`](../../backend/app/services/reviews.py) 和 [`profile.py`](../../backend/app/schemas/profile.py) 多处直接使用 `datetime.now(UTC).date()` 作为业务日期。

最小改进：

- 建立唯一的产品日期/用户时区服务；MVP 可明确固定 `Asia/Shanghai`，以后再扩展用户时区。
- 时间戳继续使用 UTC，只有“今天、任务日期、规划窗口、复盘日期”等日历业务语义使用产品时区。

验收标准：

- 覆盖上海时间 00:30、07:59、08:00 和 23:59 的 Today、Profile、Plan、Review 测试。
- 前后端展示日期和查询日期完全一致。

## 4. P1：核心体验与可信度

### P1-1 首页没有实现设计中的“唯一最高优先级下一步”

设计要求首页依次优先展示：继续未完成面试、查看已就绪报告、完成今日 Task、开始 Retest、首次导入材料。但当前首页总是展示“开始面试”和“继续或查看历史场次”两个静态按钮，并没有查询 Interview 数据来确定下一步。

证据：[`TodayPage.tsx`](../../frontend/src/pages/TodayPage.tsx) 没有使用 `useInterviews`；与 [`career-coach-v2-design.md`](../v2/career-coach-v2-design.md) 的首页优先级不一致。

建议：首页只给一个主 CTA，并按服务端可恢复状态排序；其他动作降为次级链接。未完成 Session 应直接显示当前题数，已完成报告应直接进入报告。

### P1-2 第一次面试路径仍然割裂

没有材料的用户从首页进入面试设置后，只看到“请先在求职材料保存简历和 JD”，没有直接跳转按钮，也不会保留本次面试意图。用户需要自行切换导航、填写两张表，再返回面试设置重新选择版本。

证据：[`InterviewSetupPage.tsx`](../../frontend/src/pages/InterviewSetupPage.tsx) 与 [`MaterialsPage.tsx`](../../frontend/src/pages/MaterialsPage.tsx)。

建议：第一次使用采用连续向导：简历 → JD → 面试类型 → 创建 Session。已有材料的用户继续使用版本选择页；新建材料后应默认选择刚创建的版本。

### P1-3 报告有结构化证据，但用户界面没有把证据讲清楚

当前报告把 `evidence_turn_ids` 直接显示为 UUID，并把 `dimension`、`claim.verdict` 等内部英文枚举直接暴露给用户。逐题分析只显示问题、原回答和 improvement actions，后端已经生成的 factual findings、缺失要点、建议回答结构、限制说明和 strengths 没有被充分呈现。

证据：[`InterviewReportPage.tsx`](../../frontend/src/pages/InterviewReportPage.tsx)；后端丰富契约见 [`interviews.py`](../../backend/app/schemas/interviews.py)。

建议：

- Turn ID 映射成“第 N 题 + 问题摘要”，点击可展开原回答关键句和判断依据。
- 所有用户可见枚举使用中文标签。
- 报告首屏保持“结论 → 证据 → 行动”，技术字段和置信度解释放入展开区域。
- 将 strengths、factual findings、missing key points、suggested outline 和 limitations 以渐进方式展示，而不是丢弃。

### P1-4 报告后的异步动作缺少统一进度和错误恢复

“加入训练计划”在 replan 模式会返回一个新的 Run，但页面只显示“新计划正在生成”，没有订阅该 Run 或提供直达进度入口。创建 Memory Candidate、预览/确认训练、Retest、Resume Assessment 等 mutation 也大多缺少明确错误信息。Retest 创建后直接进入房间页，但没有传递 Run ID，短时间内会把正常生成中的 draft 展示成“第一题可能生成失败”。

证据：[`InterviewReportPage.tsx`](../../frontend/src/pages/InterviewReportPage.tsx) 与 [`interviews.ts`](../../frontend/src/api/interviews.ts)。

建议：所有会启动 Run 的动作复用一个 `RunProgress` 交互；成功后给出权威落点，失败时保留用户选择并给出可重试原因。

### P1-5 面试历史页仍像内部调试列表

列表直接显示 `draft`、`active`、`report_generating`、`aborted` 等内部状态，不显示岗位、公司、材料版本或创建时间；页面也没有 loading/error/retry 状态。多场面试后，用户难以区分记录。

证据：[`InterviewPage.tsx`](../../frontend/src/pages/InterviewPage.tsx)。

建议：显示目标岗位/公司、面试类型、日期、题数和用户动作状态；将状态映射为“正在生成第一题 / 继续面试 / 正在生成报告 / 查看报告 / 已结束”；补加载失败重试。

### P1-6 隐私承诺超过了当前可用的数据控制能力

ResumeVersion 和 JobTarget 模型已经有 `deleted_at`，但 API 和前端没有删除/停用入口；面试记录也没有用户可见删除入口。“隐私与数据控制”实际仍跳到 Memory 页面。与此同时，身份是浏览器设备绑定的 Guest：清理浏览器数据或更换设备后，长期训练记录无法恢复。

证据：[`resume.py`](../../backend/app/models/resume.py)、[`resumes.py`](../../backend/app/api/resumes.py)、[`MyPage.tsx`](../../frontend/src/pages/MyPage.tsx) 和 [`auth.ts`](../../frontend/src/api/auth.ts)。

建议在邀请外部用户前至少提供：

- 材料软删除、单场面试删除或隐藏、清除全部个人数据；
- 数据保留范围、模型调用范围和音频临时处理说明；
- Guest 数据仅当前浏览器可恢复的显著提示；
- 若产品要长期使用，再增加可恢复账号或导出/迁移机制。

## 5. P2：交付与工程治理

### P2-1 当前工作区同时跨越三个 Batch，增加回归与审查成本

当前未提交工作区同时包含 `20260828_0031`、`20260829_0032`、`20260830_0033` 三个迁移，以及 Interview 核心链路、长期训练闭环、Audio、Resume Assessment 和 Eval 扩展。工作区有约 88 条变更/未跟踪状态记录。

这与仓库“一个 stage 或 vertical slice 一次实施”和设计文档“不自动跨 Batch”的约束不一致。虽然测试通过，但很难独立回答 Batch 1 是否已经达到真实用户价值门槛，也增加了回滚和定位问题的成本。

建议：按 Batch 拆分可审查提交和发布开关；先让 Batch 1 的刷新恢复、真实 Provider 与用户价值验证通过，再默认开放 Batch 2/3。

### P2-2 Alembic 仍报告循环依赖警告

完整验收成功，但 Alembic 输出 `agent_runs, goal_briefs, plans` 存在无法排序的循环外键警告。当前不是运行失败，不过警告明确说明未来版本可能升级为错误。

建议将其登记为迁移治理债务，核对 `use_alter`、约束创建顺序和 downgrade；不要等依赖升级时再处理。

### P2-3 前端关键恢复测试覆盖不足

当前前端共有 33 个测试，但 Interview Room 只有 1 个测试，Report 只有 1 个测试；没有覆盖第一题生成中刷新、回答分析中刷新、跳过后刷新、报告动作 Run、Retest 生成中状态和本地日期边界。

建议优先增加状态矩阵测试，而不是继续增加页面数量。

## 6. 已确认的优点

1. Session 与短生命周期 AgentRun 的边界合理，用户等待回答时不会长期占用 Run。
2. 原回答先持久化再分析，分析失败不会丢失用户输入，并有后端失败重试路径。
3. Interview、Turn、Resume、JobTarget 和 Run 的用户隔离、幂等、乐观锁与唯一终态约束已有测试支撑。
4. Report → MemoryCandidate/Task/Plan 需要用户确认，没有静默改写当前训练安排。
5. Retest 比较包含“证据不可比”状态，避免无依据宣称改善。
6. Audio 不保存原始媒体，ASR 失败可保留文本兜底，方向符合 MVP 边界。
7. 五个一级导航已经从旧的 Plan/Review/Memory 平铺方式收敛为首页、面试、成长、材料、我的。
8. 正式 Interview 数据集包含来源校验、信息不足、有限追问、报告、简历主张和 Prompt Injection 场景，并正确标记为仍需人工校准。

## 7. 验证记录

### 7.1 仓库完整验收

执行：

```powershell
.\scripts\check.ps1
```

结果：

- Ruff：通过；
- Mypy strict：通过，298 个源文件无问题；
- Alembic upgrade/check：通过，无新增升级操作；
- Pytest：696/696 通过；
- 旧确定性 Eval：Stage 5 30/30、Stage 6 12/12 通过；
- 前端 Vitest：13 个测试文件、33/33 测试通过；
- TypeScript/Vite 生产构建：通过。

非阻塞警告：Alembic 循环表排序警告；React Router v7 future flag 警告。

### 7.2 Interview 正式数据集

执行：

```powershell
cd backend
.\.venv\Scripts\python -m evals.v2.interview_cli
```

结果：18/18 通过，`deterministic=true`，同时明确输出：

```text
diagnostic_only=true
human_calibration_required=true
```

这证明 Mock/确定性契约可运行，不证明真实 Provider 质量或真实用户价值已经达标。

## 8. 建议实施顺序

1. 修复所有 Interview Run 的 URL/资源级刷新恢复，并补状态矩阵测试。
2. 拆开 Interview Profile 与 Planning Window，全局路由不再依赖有效规划日期。
3. 统一 Asia/Shanghai 产品日期语义和边界测试。
4. 首页实现唯一最高优先级下一步。
5. 合并首次材料导入与面试设置漏斗。
6. 重做报告证据呈现和异步动作进度/错误反馈。
7. 补材料、面试和账号层面的隐私与数据控制。
8. 冻结新功能，使用真实 Provider 完成 Golden Cases 人工审查及 5～10 名用户价值验证。

达到前 3 项后，可进入受控内部试用；达到前 7 项且真实质量门槛通过后，再考虑公开 Beta。

## 9. 审查边界

本次没有修改业务代码、数据库模型或 API，只新增本报告。审查基于当前本地工作区和 Mock/确定性环境；未进行真实模型付费调用、跨设备账号恢复测试、浏览器人工视觉走查或真实求职用户访谈，因此不对线上 AI 质量、转化率或长期留存作结论。
