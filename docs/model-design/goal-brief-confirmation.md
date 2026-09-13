# Goal Brief 目标澄清与确认设计

## 目的

用户提出职业规划、项目设计、岗位投递、面试准备或技能转型等开放目标时，系统不应立即执行计划生成。系统先形成可检查的 `GoalBrief`，信息不足则按目标类型追问，信息足够则展示摘要和系统假设；只有用户明确确认后才创建 `AgentRun`。

## 职责边界

- `GoalBrief` 负责目标字段、信息充分性、追问、版本冲突和确认状态。
- `objective_type` 取 `career_plan/project/application/interview/skill_transition`，各类型使用独立的默认交付物、成功标准和追问策略，不能统一套用项目模板。
- Goal Understanding Provider 负责结构化抽取，并接收用户已确认的可用天数与每日预算做可行性判断。配置真实 LLM 时使用 LLM；未配置或真实 Provider 失败时使用确定性规则。
- 任何 Goal Understanding Provider 调用前必须先通过共享确定性安全门禁；高风险输入不得发送给外部模型。
- 充分性策略由应用服务确定，LLM 无权把草案标记为已确认。
- `AgentRun` 只接收已经确认的规范化目标并负责可靠执行。
- 总体周期只由画像中的开始/结束日期闭区间推导，用户在自然语言中提到的周数不能覆盖或延长这个硬边界。`Plan` 按 1–8 个周期展示总体重点，并展开从开始日期起固定 7 天的任务；最后不足 7 天时使用实际剩余天数。每日或整周任务提前完成都不向后滚动补位，必须等固定周期结束后才能由用户确认生成下一周期。
- 当 LLM 判断目标在现有日期与投入下偏紧或不现实，Goal Brief 必须在确认前展示理由和“日期不变时如何缩小范围”的受限方案。用户继续确认表示接受该受限方案；Agent 仍不得越过结束日期。

## 最小充分性

必须明确目标类型、目标岗位和本次具体目标。周期由必填开始/结束日期确定，不是模型建议项；能力重点、相关技能范围、交付物与成功标准可以使用按目标类型生成且清晰展示的系统建议，用户可在确认前修改。画像中的岗位方向可以补足目标岗位，但不会被当成用户对本次草案的确认。

## 状态与可靠性

状态机见 [goal-brief-confirmation-flow.mmd](./state-machines/goal-brief-confirmation-flow.mmd)。写接口携带版本号；创建接口使用 `Idempotency-Key`；每位用户最多存在一个未结束草案。确认时在同一数据库事务中更新 `GoalBrief` 并创建带唯一 `goal_brief_id` 的 `AgentRun`，事务提交后再唤醒持久执行器。

## API

- `POST /api/v1/goal-briefs`：创建并分析目标草案。
- `GET /api/v1/goal-briefs/{id}`：恢复草案。
- `POST /api/v1/goal-briefs/{id}/refine`：补充或修改草案。
- `POST /api/v1/goal-briefs/{id}/confirm`：用户确认并创建 Agent Run。
- `POST /api/v1/goal-briefs/{id}/cancel`：取消草案。
- `GET /api/v1/me`：通过 `active_goal_brief` 恢复刷新前状态。
