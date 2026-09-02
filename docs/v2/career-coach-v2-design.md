# Career Planning Buddy V2 最终设计

> 状态：最终设计审查稿  
> 范围：产品、架构、数据模型、API、Agent、Context、Memory、Eval 与实施批次  
> 本文不包含代码实现或数据库迁移。

## 1. 最终决策摘要

V2 的产品定位收敛为：

> **一个基于简历和目标 JD 进行证据化模拟面试、把真实薄弱点转成训练任务，并通过下一轮面试验证改善的 AI 求职教练。**

V2 只围绕一条主线展开：

```text
Resume / JD
    → 文本模拟面试
    → 基于原回答的逐题分析与有限追问
    → 有证据的 Interview Report
    → 经用户确认的 Weakness / MemoryCandidate / 训练动作
    → Task / Plan
    → Retest
    → 跨场次改善比较
```

以下既有决策继续保留：

1. `InterviewSession` 是跨多轮交互的业务对象，`AgentRun` 是一次短生命周期 AI 技术执行记录；一个 Session 对应多个 Run。
2. 用户等待和回答发生在 AgentRun 之外；不在 LangGraph 中实现长驻 `wait_answer`。
3. 复用现有 AgentRun 生命周期、PostgreSQL lease、NodeRunner、预算、取消、快照、唯一终态、持久化 SSE、Provider Protocol、Tool Registry 和 Trace。
4. 复用现有 L1/L2/L3 Memory，不新增第四套 Interview Memory。
5. 复用现有 Plan、Task、TaskAdjustment、Review/Replan，将计划从产品起点改为真实表现的反馈结果。
6. 复用 Eval Harness 的 Experiment、Trial、Fixture、Provider audit、Evidence、Grader、Pairwise 和校准能力。
7. MVP 不引入 Redis、Celery、Kafka、多 Agent、微服务或对象存储。
8. Video 暂缓；不推断紧张、自信、人格等心理状态。

## 2. 本轮收敛审查结论

### 2.1 上一版存在的过度设计

上一版方向正确，但有四处可以继续简化：

- `Resume + ResumeVersion` 对首个闭环而言可以压缩为一张不可变版本表；不需要先创建一个几乎没有业务行为的 Resume 聚合根。
- Question、Answer、Analysis 拆为三张表会增加事务、排序和状态同步成本；MVP 一问一答使用 `InterviewTurn` 即可。
- InterviewReport 不需要单独建表；当前每场只保留一个权威报告，将状态、版本和结构化结果放在 `InterviewSession` 上即可。
- `SkillGap`/`Weakness` 不需要独立业务表；它首先是 Report 中有证据的结构化观察，长期化后进入现有 `MemoryCandidate/Memory`，行动化后进入现有 Task/Plan。

因此 Batch 1 只新增四张业务表：

```text
resume_versions
job_targets
interview_sessions
interview_turns
```

Batch 3 仅当需要保存同一 Resume/JD 的重复评估历史时，再新增 `resume_assessments`。如果当时只需要即时展示，仍可不建表。

### 2.2 用户确认流程收敛

只保留会产生长期或较大副作用的确认：

| 操作 | 是否二次确认 | 理由 |
|---|---:|---|
| 保存 Resume/JD | 否 | 保存按钮本身就是明确操作 |
| 开始面试 | 否 | 用户已选择类型和材料并点击开始 |
| 提交答案 | 否 | 提交即表示完成本题回答 |
| 跳过/结束面试 | 结束可用轻量确认 | 防止误触，但不进入复杂确认工作流 |
| 生成 Interview Report | 否 | 面试完成后的自然结果 |
| 生成 MemoryCandidate | 否 | Candidate 不会进入长期上下文 |
| Candidate → Memory | 是 | 涉及长期、可能敏感的能力判断 |
| Report → Task/Plan | 是，一次批量确认 | 防止系统静默改变当前训练安排 |
| 创建 Retest | 否 | 用户点击复测即为明确操作 |
| 应用简历改写 | 是，通过“保存为新版本”表达 | 不原地覆盖既有 ResumeVersion |

不做逐题确认、逐弱点确认、报告确认、追问确认。Report 页面允许用户一次选择最多 3 个训练建议并点击“加入训练计划”。

### 2.3 产品主线收敛

V2 不是“求职全流程平台”，也不是 Interview、Resume、Memory、Plan、Audio 五个并列工具。所有模块必须回答同一个问题：

> **用户在目标岗位面试中哪里表现不足，系统能否用证据帮助他训练，并验证下一次是否改善？**

不直接服务于该问题的功能不进入前三个 Batch，例如岗位投递管理、招聘信息聚合、公司面试流程预测、通用聊天、实时视频和自动简历投递。

## 3. 产品边界与核心用户路径

### 3.1 第一次使用

```text
完成最小 Profile
→ 粘贴简历文本（文件解析不是 Batch 1 阻塞项）
→ 粘贴目标 JD
→ 选择“目标岗位综合”或“简历深挖”
→ 开始 4～6 题文本面试
```

现有 Onboarding 保留目标方向、阶段和时间预算。自评技能只作为辅助信息，不作为能力事实。为兼容现有 Plan，开始/结束日期暂不删除，但不得成为开始第一场面试的额外门槛。

### 3.2 面试中

```text
系统生成当前问题
→ 用户提交文本回答
→ 短生命周期 interview_answer AgentRun
→ 保存逐题分析
→ 根据回答决定：追问 / 下一主题 / 完成
```

约束：

- 每场默认 4 题，最多 6 题；
- 每个主题最多 1 次追问；
- 整场追问最多 2 次；
- 用户可以跳过或提前结束；
- 先持久化回答，再启动分析 Run；分析失败不得丢失答案；
- 刷新页面以 `InterviewSession/Turn` 资源为权威恢复，不依赖前端内存或 SSE。

Batch 1 只提供两种入口：

1. 目标岗位综合面试：技术、项目和 Resume/JD 匹配问题混合；
2. 简历深挖：围绕简历项目和技术声明追问。

HR/行为面、自定义面试、公司专属面试暂不单独产品化。

### 3.3 面试后

Report 按“结论 → 证据 → 行动”展示：

1. 最值得改进的 1～3 个问题；
2. 每个问题对应的题目、原回答和判断依据；
3. 逐题分析；
4. 建议训练动作；
5. Batch 2 起提供加入计划和针对性复测；
6. Batch 3 起展示 Resume Claim Validation 和 Audio 指标。

不把一个不透明总分作为首屏核心。允许显示少量维度等级，但必须能够回溯到具体 Turn。

## 4. MVP 成功标准

成功标准分为三层。只有工程验收通过，才能进行 AI 质量和用户价值验证；三者不得互相替代。

### 4.1 Batch 1 工程验收门槛

以下条件必须全部满足：

1. 用户可用 Resume 文本和 JD 创建一场 4 题面试，并完成“首题 → 回答 → 分析 → 有限追问/下一题 → Report”的完整链路。
2. 每次 AI 操作都对应独立 AgentRun；每个 Run 只有一个 terminal event，非 heartbeat SSE 事件均先持久化。
3. 用户等待回答期间没有 pending/running Run；刷新或 SSE 断线后可从数据库恢复到正确问题和状态。
4. 每个 Turn 保存原问题、原回答、分析结果、问题来源和父追问；Report 中所有证据引用均指向当前用户的有效 Turn。
5. 相同答案 Idempotency-Key 不会生成重复 Turn、重复分析或重复下一题。
6. 用户 A 无法读取、回答、结束或引用用户 B 的 ResumeVersion、JobTarget、Session、Turn 和 Run。
7. Mock Provider 下 Schema、Service、Repository、API、确定性 Node、SSE、取消、失败重试测试全部通过。
8. 真实 Provider 输出解析失败最多格式修复一次；失败时保留业务状态并允许重试，不伪造分析或报告。

### 4.2 AI 质量发布门槛

Batch 1 先使用一组不少于 12 条的内部 Golden Cases 做人工审查；Batch 3 将其正式迁入 Eval Harness。发布演示前要求：

- 问题能引用有效 Resume/JD 内容，不能凭空添加经历；
- 追问明确基于上一回答中的内容，而不是通用追问模板；
- 正确回答不能被报告捏造为错误；
- 信息不足必须标记为 `insufficient_evidence`，不能标记为 `unsupported`；
- 每个高严重度技术错误必须带依据或明确置信度限制；
- Report 的 Top Weakness 必须引用至少一个 Turn；
- 训练建议必须可执行且能够映射为现有 Task。

Golden Cases 中任何“虚构用户经历、无证据高严重度批评、跨用户数据、一次错误直接写长期 Memory”均为零容忍硬失败。

### 4.3 小规模用户价值验证

这不是上线效果声明，而是 V2 是否值得继续投入的验证门槛。建议邀请 5～10 名目标用户完成至少一场面试，记录：

- 面试完成率；
- 报告中是否至少有一条用户认可且此前未明确意识到的问题；
- 用户能否从证据理解系统为何作出判断；
- 用户是否愿意选择一个建议继续训练或复测；
- 与直接使用通用聊天模型相比，用户是否感知到 Resume/JD、历史证据和闭环的差异。

若用户只把它评价为“更复杂的 ChatGPT 面试 Prompt”，则 Batch 2 不应以增加功能掩盖产品问题，应先修正提问和报告质量。

## 5. 收敛后的 Domain Model

### 5.1 `ResumeVersion`

MVP 不创建单独 `Resume` 聚合。每条记录是不可变版本：

```text
id, user_id
label
source_type: pasted_text | uploaded_file
source_text
structured_json
content_hash
parent_version_id nullable
created_at
deleted_at nullable
```

规则：

- 编辑即创建新版本，不原地修改；
- `structured_json` 保存 section 和 Batch 3 所需 claim，不为 section/claim 单独建表；
- Session 引用固定 `resume_version_id`，后续新版本不改变历史面试；
- Batch 1 只要求粘贴文本。Batch 3 已增加 PDF/DOCX/TXT 的内存解析和确认预览；不保存原文件、不做 OCR，也不阻塞核心面试。

### 5.2 `JobTarget`

```text
id, user_id
title, company nullable
jd_text
requirements_json
content_hash
created_at
deleted_at nullable
```

JobTarget 在被 Session 引用后视为冻结；编辑创建新记录。MVP 不拆 `JobDescription` 和 `JobTargetVersion`。

### 5.3 `InterviewSession`

```text
id, user_id
resume_version_id
job_target_id
interview_type: role_focused | resume_deep_dive
status: draft | active | report_generating | completed | aborted
question_limit, followup_limit
asked_question_count, followup_count
current_turn_id nullable
context_summary_json
report_status: not_requested | generating | ready | failed
report_version nullable
report_json nullable
report_run_id nullable
comparison_session_id nullable
version
started_at, completed_at, created_at, updated_at
```

Report 放在 Session 上，因为 MVP 每场只有一个权威报告。`failed` 状态可以重试并更新 `report_run_id`；一旦 `ready` 即视为不可变，不提供重跑入口。未来若真实需求要求保存同场多版报告，再拆独立版本表，不能悄悄改变用户已看到的结论。

### 5.4 `InterviewTurn`

```text
id, user_id, session_id
ordinal
parent_turn_id nullable
topic_key
question_type
question_text
question_sources_json
question_fingerprint
answer_text nullable
answer_status: pending | submitted | skipped
analysis_status: not_started | running | ready | failed
analysis_json nullable
question_run_id
analysis_run_id nullable
version
answered_at nullable
created_at, updated_at
```

不单建 Question、Answer、Analysis 表。完整原回答保留在 Turn；较早 Turn 进入 Prompt 时使用摘要，但摘要不得替代权威原文。

### 5.5 不新增的模型

| 模型 | 处理方式 |
|---|---|
| InterviewQuestion | 合并到 InterviewTurn |
| InterviewAnswer | 合并到 InterviewTurn |
| InterviewAnalysis | `InterviewTurn.analysis_json` |
| InterviewReport | `InterviewSession.report_json` |
| SkillGap/Weakness | Report 中的结构化 observation |
| InterviewMemory | 使用现有 MemoryCandidate/Memory |
| JobDescription | 合并到 JobTarget |
| ResumeClaim | `ResumeVersion.structured_json` 内稳定 claim_id |

### 5.6 AgentRun 扩展

保留现有 AgentRun 技术语义，新增：

```text
run_kind:
  planning
  interview_start
  interview_answer
  interview_report
  resume_assessment   # Batch 3

interview_session_id nullable
interview_turn_id nullable
source_interview_report_session_id nullable  # Batch 2，仅 planning/retest 使用
```

`result_kind` 最小扩展为：

```text
interview_turn
interview_report
resume_assessment
```

完整对象仍通过资源 API 查询；Run result payload 只返回资源 id、版本和短摘要。

MVP 继续保留“同一用户最多一个 active AgentRun”的约束。个人项目没有必要允许用户同时生成计划、分析回答和运行报告。

## 6. 状态、事务和幂等

### 6.1 Session 状态

```text
draft → active → report_generating → completed
           └──────────────────────→ aborted
report_generating → active  # 报告失败后恢复，可重试或继续补题
```

### 6.2 回答事务

`POST /interviews/{id}/answers` 在一个短事务内：

1. 按 `user_id` 锁定 Session 和当前 Turn；
2. 校验 Session 为 active、Turn 为 pending、version 和 Idempotency-Key；
3. 保存 answer_text，状态改为 submitted；
4. 创建 `interview_answer` AgentRun；
5. 写 `run.created`；
6. 提交后调度 Run。

Run 完成时，Finalizer 在一个事务中：

1. 写当前 Turn 的 `analysis_json/status`；
2. 若继续，创建唯一的下一 Turn；若结束，进入 report_generating；
3. 更新 Session version/current_turn/counters；
4. 完成 AgentStep、Run result 和唯一 terminal event。

模型只产生候选分析和候选下一题，不直接写 ORM。

### 6.3 失败语义

- 回答已保存但分析失败：Turn 的 `analysis_status=failed`，Session 仍可恢复，用户可重试，不要求重答；
- 首题生成失败：Session 保持 draft，可重试或删除；
- Report 失败：保留全部 Turn，`report_status=failed`，可重新生成；
- 用户取消 Run：不回滚已经提交的原回答；
- 用户结束面试：当前空白题不计为回答，直接生成已有 Turn 的报告。

## 7. Agent 与 LangGraph 设计

不创建一套并行 Runtime，只新增 Interview 专用短 Graph，并共享现有执行设施。

### 7.1 `interview_start` Graph

```text
risk_gate
→ build_interview_context
→ select_question
→ validate_question
→ persist_interview_turn
```

### 7.2 `interview_answer` Graph

```text
risk_gate
→ build_interview_context
→ analyze_answer
→ decide_next_step
→ validate_turn_analysis
→ persist_analysis_and_optional_next_turn
```

`decide_next_step` 是受控决策，不是第二个 Agent：

- 达到题数上限 → finish；
- 当前回答暴露高价值未解决点且追问预算未耗尽 → followup；
- 否则 → next topic；
- 用户显式结束 → finish。

### 7.3 `interview_report` Graph

```text
build_report_context
→ aggregate_report
→ validate_report_evidence
→ persist_report
```

### 7.4 Provider 边界

复用 `RuntimeProviderRegistry` 和底层 LLMClient/audit，新增一个 `InterviewProvider` Protocol。不要把 Interview 输出塞进强依赖 `PlanningContext/PlanCandidate` 的 `PlanningProvider`。

结构化输出最多格式修复一次；业务 Validator 不通过时允许一次专用修复，修复不得重新调用 Tool。

### 7.5 Tool 使用

Batch 1 默认不开放 Web Search。问题和初步分析只使用冻结的 Resume/JD、当前回答和少量已确认 Memory。

只有当技术正确性判断明确需要外部知识时，才按顺序考虑：

1. L3 `rag_retrieve`；
2. Batch 3 Eval 证明有收益后再允许受控 `web_search`。

这样可以避免首个 MVP 被知识库建设拖慢，也降低“搜索片段被当作技术真值”的风险。

## 8. Interview Context Engineering

### 8.1 每次必须进入

- Session id/version、面试类型、题数和追问剩余预算；
- 当前题和当前原回答；
- 已问题目的 fingerprint/topic；
- 最近 2 个完整 Turn；
- 更早 Turn 的确定性摘要；
- 与当前主题相关的少量 Resume claim、JD requirement；
- 输出 Schema、评价边界和证据规则。

### 8.2 只在开始时解析和冻结

- 完整 ResumeVersion；
- 完整 JD；
- Resume section/claim；
- JD requirement；
- 面试配置。

后续 Prompt 只使用选中的片段和 hash/id，不重复注入完整文档。

### 8.3 按需检索

- 与当前 topic 相关的长期 Weakness Memory；
- 可比历史 Session 的摘要；
- L3 技术知识；
- Batch 2 报告生成时读取当前 Plan/Task。

### 8.4 压缩规则

- 最近 2 个 Turn 保留问题、原回答和短分析；
- 更早 Turn 只保留 topic、结论、已覆盖点、未解决追问；
- 不把 Report、历史 transcript、全部 Memory 和完整 Resume/JD 每次全量塞入；
- 原始 answer 永远保留在数据库，Prompt 摘要只是上下文投影；
- Snapshot 冻结被引用的 ResumeVersion、JobTarget、Memory id/version/hash 和 Turn ids。

扩展现有 `context_selection.py` 的排序/预算思想和 `context_compression.py` 的确定性压缩方式，但使用独立 `InterviewContext`，不扩张 `PlanningContext`。

## 9. Answer Analysis 与 Interview Report 契约

### 9.1 单题分析

```text
question
answer_text
covered_key_points[]
missing_key_points[]
factual_findings[]:
  claim
  verdict: correct | incorrect | partially_correct | insufficient_evidence
  severity
  confidence
  rationale
  evidence_refs[]
answer_structure:
  conclusion_first
  logical_flow
  specificity
  concision
improvement_actions[]
suggested_outline[]
followup_reason nullable
limitations[]
```

Batch 1 不要求每题生成完整“标准答案”。最多提供建议结构和关键点，避免高成本、长文本和错误权威感。

### 9.2 整场报告

```text
overall_summary
strengths[]
weaknesses[]:
  weakness_key
  topic
  dimension
  severity
  confidence
  evidence_turn_ids[]
  status: observed | repeated | improving
dimension_summary[]
recommended_training_actions[]:
  title
  starter_action
  deliverable
  estimated_minutes
  source_weakness_keys[]
resume_claim_findings[]       # Batch 3 完整启用
comparison nullable           # Batch 2
limitations[]
```

规则：

- 每个 Weakness 至少引用一个 Turn；
- 高严重度事实错误必须有可靠依据或明确低置信度；
- 单题观察默认 `observed`，不能直接写成长期能力事实；
- Report 只给 1～3 个优先训练建议；
- 不展示无证据的“紧张、不自信、抗压差”等心理判断。

## 10. Memory、Plan 与 Retest 闭环

该部分在 Batch 2 实现。

### 10.1 Weakness 不是新存储系统

Weakness 首先存在于 `InterviewSession.report_json`。满足下列任一条件时才生成 `MemoryCandidate`：

- 同场多个 Turn 对同一能力给出一致证据；
- 与历史已确认 Weakness 再次一致；
- 用户主动选择“长期关注”。

Candidate 保存：

```text
weakness_key
claim
confidence
evidence_session_ids[]
evidence_turn_ids[]
observation_count
first_observed_at
last_observed_at
```

用户确认后进入现有 Memory。相同 `weakness_key` 优先更新/合并已有 Memory，不无限新增。

### 10.2 一次批量训练确认

Report 页面允许用户勾选最多 3 个建议，然后一次点击“加入训练计划”：

- 当前周期存在可调整 pending Task：优先创建一个或多个 `TaskAdjustmentProposal`；
- 当前计划已结束或需要明显改变重点：创建一个带 `source_interview_report_session_id` 的 replan Run；
- 所有变更在用户确认前只是 Proposal；
- 不要求用户再为每个 Task 单独通过第二轮确认。

实现时由一个应用 Service 在同一用例中完成 Proposal 的展示和批量确认，避免确认链膨胀。

### 10.3 Retest

Retest 是新的 InterviewSession：

- 指向同一或显式选择的新 ResumeVersion/JobTarget；
- `comparison_session_id` 指向基线场次；
- Context 优先选择待复测 `weakness_key`；
- 问题不要求逐字相同，但必须测量相同 topic/dimension；
- 比较结果只在题目目标可比时输出。

比较状态：

```text
improved | unchanged | regressed | insufficient_comparable_evidence
```

不生成一个跨所有能力的虚假综合成长分。

## 11. API Boundary

### Batch 1

| API | 行为 | 返回模式 |
|---|---|---|
| `POST /api/v1/resume-versions` | 保存粘贴文本或新版本 | 同步 HTTP |
| `GET /api/v1/resume-versions` | 查询当前用户版本 | 同步 HTTP |
| `POST /api/v1/job-targets` | 保存 JD | 同步 HTTP |
| `GET /api/v1/job-targets` | 查询目标岗位 | 同步 HTTP |
| `POST /api/v1/interviews` | 建 Session 并启动首题 Run | 202 + run/events URL |
| `GET /api/v1/interviews/{id}` | 恢复 Session、当前 Turn、报告摘要 | 同步 HTTP |
| `POST /api/v1/interviews/{id}/answers` | 保存答案并启动分析 Run | 202 + run/events URL |
| `POST /api/v1/interviews/{id}/finish` | 结束并启动报告 Run | 202 + run/events URL |
| `POST /api/v1/interviews/{id}/report/retry` | 重试失败报告 | 202 |

继续复用：

```text
GET  /api/v1/agent-runs/{run_id}
GET  /api/v1/agent-runs/{run_id}/events
POST /api/v1/agent-runs/{run_id}/cancel
```

### Batch 2

```text
POST /api/v1/interviews/{id}/memory-candidates
POST /api/v1/interviews/{id}/training-actions/preview
POST /api/v1/interviews/{id}/training-actions/confirm
POST /api/v1/interviews/{id}/retest
GET  /api/v1/interviews/{id}/comparison
```

现有 MemoryCandidate confirm/reject API 继续复用，不新增第二套同义端点。

### Batch 3

```text
POST /api/v1/resume-assessments
GET  /api/v1/resume-assessments/{id}
POST /api/v1/interviews/{id}/audio-answers
```

Audio 第一版使用单题短音频 `multipart/form-data`。没有对象存储时，请求期临时文件在 ASR 后立即删除，只保存 transcript、时间戳和派生指标。

## 12. 前端信息架构

最终一级导航：

```text
首页 | 面试训练 | 成长 | 求职材料 | 我的
```

不再把 Plan、Review、Memory、Interview、Resume 全部平铺为一级入口。

### 首页

只展示当前最重要的一个动作，优先级：

1. 继续未完成面试；
2. 查看已就绪报告；
3. 完成今日训练 Task；
4. 针对已训练 Weakness 开始 Retest；
5. 无数据时导入 Resume/JD 并开始首场面试。

### 面试训练

- 开始面试；
- 未完成 Session；
- 历史场次；
- Batch 2 起展示最近 Weakness 和复测入口。

### 成长

整合现有 Plan、Task、Review 和跨场次改善。Memory 的长期能力投影可以展示，但确认、停用和删除仍放“我的”。

### 求职材料

管理 ResumeVersion、JobTarget；Batch 3 展示 Claim Validation。它不是通用简历编辑器。

### Interview Room

Batch 1 只需要当前问题、回答框、题数/追问进度、提交、跳过、结束和 AI 状态。摄像头、实时分数、复杂计时仪表盘均不进入 MVP。

### Interview Report

按以下顺序：Top Weakness → 证据 → 逐题分析 → 训练建议 → Batch 2 的改善比较 → Batch 3 的 Claim/Audio。

## 13. Eval 扩展原则

Batch 3 正式扩展 Eval Harness，但 Batch 1 就必须维护 Golden Cases 和 Mock Provider 测试，不能等到 Batch 3 才开始评价质量。

不应继续向现有 Planning `EvalScenario` 堆大量可选字段。使用判别联合：

```text
PlanningEvalScenario
InterviewQuestionScenario
InterviewAnswerScenario
InterviewReportScenario
ResumeClaimScenario
```

复用：

- Experiment/Trial 生命周期；
- Fixture record/replay；
- Provider audit；
- Evidence authorization；
- System、Safety、Tool 类 Grader；
- Pairwise、position balance 和人工校准；
- 报告统计和失败分类。

新增确定性检查：

- 问题引用合法 Resume/JD claim；
- question fingerprint 不重复；
- follow-up 引用上一回答中的具体事实；
- key point/error 命中人工标注；
- 不把信息不足误判为错误；
- Report evidence turn id 有效；
- 单次错误不生成长期 Memory；
- 训练 Task 引用真实 Weakness；
- Retest 比较维度可比。

Pairwise 适合比较报告的具体性、忠实度和可执行性，不是技术正确性的唯一真值。没有足够真实人工标签时继续标记为 `diagnostic_only`。

## 14. Audio 与 Video 决策

### Audio：Batch 3

只做单题录音，不做实时语音对话。需要：

- ASR transcript；
- word/segment timestamp；
- 总回答时长；
- 有效语速；
- 长停顿；
- 回答前思考时间；
- filler word 和重复表达；
- 明确 ASR 置信度和指标局限。

ASR 在无对象存储的 MVP 中使用同步短请求，设置严格大小、格式和时长限制；若实际延迟无法接受，再修订基线设计异步媒体任务与对象存储，不能用进程内后台任务假装可靠队列。

### Video：暂缓

前三个 Batch 不做 Video，不新增 MediaRecorder 视频链路、对象存储和视觉模型。未来重新评审时只能分析可观察行为，不得从表情、低头或视线推断心理状态。

## 15. 三个实施 Batch

每个 Batch 是一个可独立验收的纵向成果。Codex 可以在 Batch 内按依赖顺序连续实施，不要求为人工协作再拆成大量微型 Phase；但仍须遵守仓库规则，编码前列出文件、迁移、API 和测试，完成后运行 acceptance commands，不自动跨 Batch。

### Batch 1：完整核心面试链路

目标：

```text
Resume/JD
→ InterviewSession
→ 文本回答
→ Answer Analysis
→ 有限追问
→ InterviewReport
```

数据变化：

- 新增 `resume_versions`、`job_targets`、`interview_sessions`、`interview_turns`；
- 扩展 `agent_runs.run_kind/interview_session_id/interview_turn_id/result_kind`；
- 不新增 Weakness、Report、Question、Answer、Analysis 独立表。

主要后端范围：

```text
backend/app/models/resume.py
backend/app/models/interview.py
backend/app/schemas/resumes.py
backend/app/schemas/interviews.py
backend/app/repositories/resumes.py
backend/app/repositories/interviews.py
backend/app/services/resumes.py
backend/app/services/interviews.py
backend/app/api/resumes.py
backend/app/api/job_targets.py
backend/app/api/interviews.py
backend/app/agent/interview_graph.py
backend/app/agent/interview_context.py
backend/app/agent/interview_nodes.py
backend/app/providers/interview.py
backend/app/prompts/interview.py
```

需要修改的现有后端范围：

```text
backend/app/models/agent_run.py
backend/app/schemas/agent_runs.py
backend/app/schemas/enums.py
backend/app/agent/executor.py
backend/app/agent/finalizer.py
backend/app/providers/registry.py
backend/app/api/dependencies.py
backend/app/api/router.py
backend/app/harness/snapshots.py
backend/app/harness/events.py
```

前端范围：

```text
frontend/src/api/types.ts
frontend/src/api/resumes.ts
frontend/src/api/interviews.ts
frontend/src/router/index.tsx
frontend/src/components/AppLayout.tsx
frontend/src/pages/InterviewPage.tsx
frontend/src/pages/InterviewSetupPage.tsx
frontend/src/pages/InterviewRoomPage.tsx
frontend/src/pages/InterviewReportPage.tsx
frontend/src/pages/MaterialsPage.tsx
frontend/src/pages/TodayPage.tsx
```

测试：Schema、Repository 用户隔离、Service 状态机/幂等、API、Mock InterviewProvider、确定性 Nodes、Finalizer 事务、SSE 回放/唯一终态、取消/失败恢复、前端刷新恢复和核心页面测试。

完成标准：满足第 4.1 节全部工程门槛，并通过第 4.2 节 Golden Cases 的硬失败检查。

### Batch 2：长期求职陪伴闭环

目标：

```text
InterviewReport
→ Weakness
→ MemoryCandidate
→ 用户一次批量确认训练动作
→ Task/Plan
→ Retest
→ 跨场次改善比较
```

数据变化：

- 优先复用现有 MemoryCandidate/Memory、TaskAdjustmentProposal、Plan、Task；
- 扩展 Memory 内容契约和候选证据；
- 为 AgentRun 增加 `source_interview_report_session_id`；
- InterviewSession 增加/启用 `comparison_session_id`；
- 原则上不新增业务表。

主要后端范围：

```text
backend/app/services/memory_candidate_distiller.py
backend/app/services/memories.py
backend/app/services/task_adjustments.py
backend/app/services/plans.py
backend/app/services/interviews.py
backend/app/agent/context_selection.py
backend/app/agent/context_compression.py
backend/app/agent/graph.py
backend/app/schemas/memories.py
backend/app/schemas/plans.py
backend/app/schemas/interviews.py
```

前端范围：Report 训练选择、MemoryCandidate 确认、Growth 页面、Retest 入口、场次比较；复用并重组现有 Plans/Reviews/Memories 页面，不创建三个同义入口。

测试：候选生成阈值、一次错误不长期化、相同 weakness 合并、批量训练确认、TaskAdjustment 与 Replan 两条路径、Plan 不静默替换、Retest 目标选择、不可比场次不输出改善、用户隔离。

完成标准：至少一个 Golden Case 能完整证明“首场发现数据库索引弱点 → 生成候选和训练任务 → 用户完成训练 → Retest 同一维度 → 输出有证据的改善比较”。

### Batch 3：Claim Validation、正式 Eval 与 Audio

目标：

```text
Resume Claim Validation
+ Interview Eval Harness
+ 单题 Audio/ASR 客观表达指标
```

Resume：

- 从 ResumeVersion 的 `structured_json` 生成稳定 claim_id；
- 建立 claim ↔ JD requirement ↔ InterviewTurn 证据；
- 输出 supported / partially_supported / unsupported / insufficient_evidence；
- 修改建议通过创建新 ResumeVersion 应用，不覆盖历史版本；
- `resume_assessments` 仅在确实需要保存多次评估历史时新增。

Eval：

- 扩展 Case 判别联合、EvidenceKind、Collector、Grader、FixtureLoader 和 TrialRunner；
- 最小正式数据集不少于 16 Case：问题 4、答案分析 6、追问 3、Memory/Plan 2、安全/注入 1；
- 使用 Pairwise 比较报告质量，并继续要求人工校准。

Audio：

- 增加 ASR Provider Protocol；
- 单题短音频 multipart 上传；
- 临时文件即时清理；
- transcript/timestamp/客观指标写入 Turn analysis；
- 不引入 Video 和心理状态推断。

完成标准：Claim 结论均能回溯到 Turn；Eval 可在 Mock/Fixture 下确定性运行并生成报告；Audio 失败不丢失文本答案且原始媒体不被持久化。

## 16. 明确不做与止损条件

前三个 Batch 明确不做：

- Video、表情识别、心理状态推断；
- 实时语音对话；
- 对象存储；
- 多 Agent；
- Redis/Celery/Kafka；
- 微服务；
- 自动投递和招聘流程管理；
- 公司专属题库大规模建设；
- 通用简历美化器；
- 无证据综合能力总分；
- 单次错误自动写长期 Memory；
- Agent 自动修改 Plan 或 Resume。

止损条件：

1. Batch 1 的报告不能稳定避免虚构错误时，先修质量和证据，不进入长期 Memory 自动化。
2. 目标用户感知不到与通用聊天模型的差异时，先强化 Resume/JD/历史证据闭环，不增加 Audio/Video。
3. Retest 题目不可比时，不输出改善结论。
4. ASR 无可靠时间戳时，只提供 transcript，不伪造停顿和语速指标。
5. 单题音频无法在同步请求限制内可靠完成时，暂停 Audio；对象存储和异步任务必须另行修订项目基线后再做。

## 17. 最终优先级

| 顺序 | 能力 | 用户收益 | 技术收益 | 工作量 | 风险 | 优先级 |
|---:|---|---|---|---|---|---|
| 1 | Session/Run 边界和短 Run | 会话可恢复、不丢回答 | 保留 Runtime 不引入长驻工作流 | 中 | 迁移边界错误 | P0 |
| 2 | Resume/JD 文本面试 | 获得个性化真实训练 | 建立 Interview 纵切 | 高 | 问题质量 | P0 |
| 3 | 逐题证据化 Analysis | 明确知道哪里有问题 | 建立结构化质量契约 | 高 | 错误批评 | P0 |
| 4 | 有限追问 | 模拟真实深挖 | 验证短 Run 多轮编排 | 中 | 通用模板追问 | P0 |
| 5 | Interview Report | 得到可行动诊断 | 聚合 Turn evidence | 中 | 退化为总分 | P0 |
| 6 | Report → Task/Plan | 建议进入行动 | 放大现有 Plan/Task | 中 | 确认过重或静默修改 | P0 |
| 7 | Retest 与改善比较 | 看见训练是否有效 | 建立跨 Session 证据 | 中 | 不可比 | P1 |
| 8 | 多证据 MemoryCandidate | 系统长期记住问题 | 强化现有 L2 | 中 | 偶然错误固化 | P1 |
| 9 | Claim Validation + Eval | 简历内容经得起追问 | 形成作品集核心质量证明 | 高 | 标注成本 | P1 |
| 10 | 单题 Audio 指标 | 改善表达方式 | 扩展 ASR Provider | 高 | 延迟、隐私、时间戳 | P2 |

## 18. 最终验收后的产品介绍

完成 Batch 1 后：

> Career Planning Buddy 会根据你的简历和目标 JD 进行结构化模拟面试，并用原回答证据生成逐题复盘报告。

完成 Batch 2 后，正式产品介绍为：

> Career Planning Buddy 是一个证据化 AI 求职教练：它通过简历定向模拟面试发现真实薄弱点，把问题转成训练任务，并在下一轮面试中验证是否改善。

与直接使用 ChatGPT 相比，成立所需的最小差异不是更像真人聊天，而是：

1. 输入材料有冻结版本；
2. 问题和评价有可追溯证据；
3. Session 可以跨轮、跨天恢复；
4. 长期能力判断需要多证据和用户确认；
5. 诊断能够进入 Task/Plan；
6. 后续 Retest 能验证同一能力是否改善；
7. 整条链路可通过 Trace、Fixture 和 Eval 复现与评价。
