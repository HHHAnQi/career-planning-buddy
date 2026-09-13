# career_planning_agent — 核心规划 Agent

## 定位

系统唯一真 Agent。它接收只读 `PlanningContext`，在 Stage 4 可自主选择白名单 Tool，最终生成结构化 `PlanCandidate`。它不直接写数据库、不改变业务状态，也不能调用副作用 Tool。

产品输出分两层：

1. **方向层**：面向 1~8 周的整体方向和每周重点；
2. **行动层**：生成从 `planning_date` 开始、最多七天且不越过目标日期的执行表，每天默认 1 个关键任务。

这样既能回答“到目标日期前怎么准备”，又避免一次生成几十个很快失效的任务。当前固定周期结算后，才通过来源计划和复盘生成下一版本。

完整循环见 [`../agent-runtime/README.md`](../agent-runtime/README.md)，Tool 契约见 [`../tools/README.md`](../tools/README.md)。

## Input

```python
class CareerPlanningAgentInput(BaseModel):
    run_id: UUID
    intent: Literal["create_plan", "replan"]
    replan_mode: Literal["initial", "continue", "adjust"]
    effective_goal_type: GoalType
    user_request: str
    planning_context: PlanningContext
    available_tools: list[ModelToolSpec]
    remaining_deadline_ms: int
```

`PlanningContext` 只包含序列化、脱敏后的画像、规划窗口、当前/来源计划、近期任务与复盘、已确认记忆和证据目录。

## 预算

| 项 | 上限 |
|---|---:|
| AgentTurn 主调用 | Stage 2/3 最多 1；Stage 4/5 最多 3 |
| 格式修复 | 全 Run 最多 1，单独计入全局预算 |
| Tool Calling 轮次 | 2 |
| 每轮 Tool 数 | 2 |
| Tool 调用总数 | 4 |
| 单 Tool 超时 | 8s |
| Agent 节点超时 | 30s |

Stage 2/3 的 `available_tools=[]`，模型必须直接返回候选；Stage 4 才开放 `memory_lookup`、`rag_retrieve`、`web_search`。

## Output

```python
class WeeklyFocusCandidate(BaseModel):
    week_index: int = Field(ge=1, le=8)
    focus: str = Field(min_length=1, max_length=160)
    success_signal: str = Field(min_length=1, max_length=200)

class PlanCandidate(BaseModel):
    plan_date: date
    horizon_start: date
    horizon_end: date
    overall_direction: str = Field(min_length=1, max_length=500)
    weekly_focus: list[WeeklyFocusCandidate] = Field(min_length=1, max_length=8)
    summary: str = Field(min_length=1, max_length=500)
    rationale: str = Field(min_length=1, max_length=2000)
    adjustment_reason: str | None = Field(default=None, max_length=1000)
    assumptions: list[str] = Field(default_factory=list, max_length=5)
    tasks: list[TaskCandidate] = Field(min_length=1, max_length=7)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=10)

class EvidenceRef(BaseModel):
    kind: Literal["memory", "experience_atom", "search_source"]
    id: UUID

class TaskCandidate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    task_type: TaskType
    scheduled_date: date
    starter_action: str = Field(min_length=1, max_length=240)
    deliverable: str = Field(min_length=1, max_length=240)
    estimated_minutes: int = Field(ge=5, le=480)
    rationale: str | None = Field(default=None, max_length=500)
```

`horizon_start/horizon_end` 必须等于 `PlanningContext.planning_window`，其中 `horizon_end` 精确等于必填的 profile.deadline，最长 8 周。

## Tool Calling 行为

1. 模型只能看到 `available_tools` 中的定义；
2. Tool 参数必须通过 Pydantic 校验；
3. 相同 `tool_name + args_hash` 在同一 Run 中复用；
4. Tool 结果以 `<evidence>` 边界回填，不具有指令权限；
5. 达到 Tool 预算后，下一轮只允许返回最终 `PlanCandidate`；
6. 模型返回自由文本、未知 Tool 或混合“Tool Call + Final”时视为结构错误。

## 业务不变量

- `weekly_focus` 覆盖规划窗口，week_index 连续且不重复；
- 每项 Task 按 `scheduled_date` 相对 `horizon_start` 计算所属 week_index，标题、启动动作、
  交付物和规划理由必须直接推进该周的 focus 与 success_signal，不得提前安排后续周工作；
- 当前周期任务的 `rationale` 必须包含第 1 周 `focus` 原文，供规则校验器做确定性对齐检查；
- 所有 Task 的 `scheduled_date` 必须连续覆盖 `plan_date` 到 `min(plan_date + 6 天, horizon_end)`；
- 执行表为 1~7 个任务，每天 1 个，每天的总时长不超过用户每日预算；
- 每项 Task 的 `starter_action` 包含 2~3 个有对象、有数量或方法的有序步骤，`deliverable` 同时写明可量化产物和通过条件；
- `evidence_refs` 只能引用产生当前候选的 Provider 调用实际可见的
  Memory/ExperienceAtom/SearchSource；Graph 不得自动补齐引用；
- 不允许自行改变用户 `goal_type`；
- create_plan 不得假装存在历史执行事实；
- `replan_mode=continue` 应延续原方向和下一周重点，不无故推翻；
- `replan_mode=adjust` 必须保留已完成事实并明确 `adjustment_reason`；
- 不重复安排近期已完成的同一交付物；
- 不输出 SQL、Shell 命令、数据库写入指令或未经证实的 URL；
- Provider Schema 解析失败最多做一次格式修复，不重新执行 Tool。

## Prompt

Prompt 位于 `backend/app/prompts/career_planning/` 并显式版本化。System 区只放角色、边界、输出契约和 Tool 规则；用户请求、画像、记忆和搜索结果全部放在不可信数据区。

Trace 必须记录：prompt_version、实际 model_id、每次 call 的 token/latency/cost、Tool round、最终 output hash，不保存 API Key 和完整敏感 Prompt。

## 失败策略

| 失败 | 行为 |
|---|---|
| Provider timeout/rate limit | 若上下文足够则模板 fallback，否则 failed |
| Tool timeout | 写 Tool 失败结果，允许 Agent 用已有证据继续 |
| Tool 全部失败 | 禁止编造来源，使用本地上下文或 fallback |
| 输出 Schema 错 | 格式修复一次，仍错交给 fallback |
| 规则校验失败 | 交给 revise_or_fallback，不在本节点无限重试 |

## 测试

- Stage 3 无 Tool 一次生成；
- 5 周截止日期得到 5 个递进 weekly focus，同时只展开当前固定周期；
- continue 按固定周边界保持方向并推进下一步；
- adjust replan 保留 completed facts 并解释调整；
- 两轮 Tool 后正常输出；
- 未知 Tool、参数越界、重复 args_hash；
- Tool 预算耗尽后仍请求 Tool；
- evidence id 伪造；
- horizon/plan_date 不匹配；
- 模型超时、Schema 错和取消。
