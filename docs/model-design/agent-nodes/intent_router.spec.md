# intent_router — 意图决策

## 定位与技术选择

当前版本使用版本化确定性规则，不调用 LLM。项目只支持“创建计划、续接计划、调整计划”三个生成动作，另有查询分流和歧义澄清；规则方案在当前规模下更稳定、可测试、低延迟，也不会产生额外模型费用。

保留结构化分类器扩展点：只有当真实流量中的低置信度比例、规则冲突率或新增意图数量持续上升，并且离线评测证明模型路由有净收益时，才引入输出同一 `IntentResult` 的 LLM/小模型分类器。模型不能绕过服务端来源计划、Review 和权限约束。

## Input

```python
class IntentInput(BaseModel):
    message: str
    hint_intent: Literal["create_plan", "replan"] | None
    profile: ProfileContext | None
    source_plan_exists: bool
    goal_type_override: GoalType | None
    forced_replan_mode: ReplanMode | None
```

`hint_intent` 是弱提示，不是命令。`forced_replan_mode` 来自服务端 Review 流程，是可信决策输入。

## Output

```python
class IntentResult(BaseModel):
    intent: Literal["create_plan", "replan", "unsupported"]
    replan_mode: Literal["initial", "continue", "adjust"]
    confidence: float = Field(ge=0, le=1)
    confidence_band: Literal["high", "medium", "low"]
    router_version: str
    matched_rule_ids: list[str]
    ambiguity_reasons: list[str]
    missing_slots: list[Literal[
        "goal_type", "stage", "time_budget_minutes", "skill_level"
    ]]
    effective_goal_type: GoalType | None
    requested_horizon_weeks: int | None = Field(default=None, ge=1, le=8)
    requires_fresh_information: bool
    method: Literal["rule", "model", "rule_fallback"]
```

## 决策优先级

| 优先级 | 条件 | 决策 |
|---:|---|---|
| 1 | 查询已有计划、任务或日程 | `unsupported`，提示使用资源页面 |
| 2 | 服务端提供 `forced_replan_mode` 且来源计划存在 | `replan`，严格采用服务端 mode |
| 3 | 来源计划存在，用户明确减量、换重点、改变方向 | `replan + adjust` |
| 4 | 来源计划存在，用户明确继续，或否定调整 | `replan + continue` |
| 5 | 来源计划存在，用户提出新的规划请求 | 默认 `replan + continue`；显式重置方向为 `adjust` |
| 6 | 无来源计划，用户明确创建职业规划 | `create_plan + initial` |
| 7 | 只有重规划措辞但无来源，或没有可支持的语义信号 | `unsupported + rule_fallback`，请求澄清 |

补充规则：

- 中英文规则采用稳定 `rule_id`，覆盖创建、查询、继续、调整、否定调整、重置方向、职业语境和问候；
- 查询的优先级高于 `hint_intent`，不会误触发生成；
- “不要调整/without changing”优先于调整词，避免否定表达被反向识别；
- `hint_intent` 与消息或服务端上下文冲突时，记录 `hint_conflicts_with_message_or_context` 并降低置信区间；
- 仅有 hint、问候或无语义文本不能启动生成，返回 `intent_uncertain`；
- 周期支持中英文 1~8 周、中文月份表达，超出范围统一裁剪；
- 只有已确认进入生成意图且 Profile 不完整时才填写 `missing_slots`；
- `requires_fresh_information` 只表达后续受控工具需求，不改变主意图。

## 置信度语义

置信度是版本内的可解释决策强度，不宣称是经过统计校准的概率：

- high：高精度规则或服务端强制上下文；
- medium：消息语义明确，但 hint 与上下文冲突；
- low：缺少来源、缺少有效语义或只能走规则兜底。

低置信度不调用规划模型，直接输出 `intent_uncertain` 澄清，从而控制误生成成本。

## Trace 与隐私

`intent_router` 的 `trace_data` 写入：`router_version`、`method`、`intent`、`replan_mode`、`confidence`、`confidence_band`、`matched_rule_ids`、`ambiguity_reasons`、`requested_horizon_weeks`、`missing_slots`、`requires_fresh_information`。

用户完整原文不重复写入 `trace_data`；路由不产生 token、模型或工具调用。新增字段位于现有 JSON Trace 和 DTO 中，不需要数据库迁移。

## 验收用例

- “你好” + `hint=create_plan` → `intent_uncertain`，不生成计划；
- “今天有什么任务” → 查询分流，不生成计划；
- “任务太多，后面每天减到半小时” + source → `replan + adjust`；
- “不要调整，继续按原计划” + source → `replan + continue`；
- 明确创建请求但 Profile 缺失 → `profile_incomplete`；
- 调整请求但无 source → `intent_uncertain`；
- 服务端 Review 强制 mode → mode 不受客户端 hint 覆盖。
