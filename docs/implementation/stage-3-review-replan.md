# Stage 3：真实模型与执行反馈闭环

## 目标

在 Stage 2 Runtime 稳定的前提下接入真实 OpenAI-compatible LLM，完成任务状态、复盘和重规划。Stage 3 不开放 Web/RAG/Memory Tool，先验证结构化规划本身。

## 必读

- `docs/model-design/agent-runtime/README.md`
- `docs/model-design/agent-nodes/career_planning_agent.spec.md`
- `docs/model-design/agent-nodes/rule_validator.spec.md`
- `docs/model-design/agent-nodes/revise_or_fallback.spec.md`
- `docs/third-party-integration/llm-provider.md`

## 实现范围

1. `LLMProvider.generate_structured/generate_agent_turn` 真实实现与 Mock 契约测试；
2. Prompt Registry、版本冻结和实际 model id 记录；
3. CareerPlanningAgent 在无 Tool 模式生成 PlanCandidate；
4. 结构格式修复一次；
5. 规则校验 + 专用 repair Prompt 一次；
6. 模板 fallback；
7. Task PATCH 状态机；
8. Review POST / GET / PATCH / DELETE；未被下一计划消费时可修改、删除，已消费后锁定；
9. `POST /reviews/{id}/start-next-plan`，只在固定周周期到期后支持 continue/adjust；任务提前全部结算不改变周期边界；
10. 新计划成功时归档被替代计划；
11. 首个任务开始时 generated → active；
12. 所有任务进入 completed/abandoned/expired 后 active → completed；
13. 本周 pending/in_progress Task 支持人工编辑和 AI 提案，经用户确认后应用；completed Task 必须先由用户撤销完成才可修改，完成事实不得被静默覆盖。
14. Task 执行步骤支持逐项、可逆、持久化勾选；全部步骤完成后才允许验收，验收通过与实际用时、Task 完成在同一事务结算。

## 模型预算

- risk classifier：0/1；
- intent router：0/1；
- CareerPlanningAgent：Stage 3 无 Tool，AgentTurn 最多 1；
- repair：0/1；
- 单 Run 全局最多 5（risk 0/1 + intent 0/1 + AgentTurn 1 + format repair 0/1 + business repair 0/1）；
- companion 使用模板，不额外调模型；
- 剩余 Deadline 不足时不得发起新调用。

## 复盘规则

Review 请求只提交用户复盘内容，完成/放弃数量由 Service 从 tasks 计算，避免客户端与数据库事实冲突。

触发重规划建议的基础规则：

- 连续两次放弃；
- 用户时间预算变化；
- 用户明确要求调整；
- 目标方向明确变化；
- 阻塞持续存在。

每日复盘不替换仍开放的固定周周期。重规划必须由用户确认，不允许后台静默替换活跃计划。

`context_builder` 对 replan 必须提供：source plan、completed facts、未完成任务、blockers、最近 review 和新时间预算。新计划不得删除已完成历史。

## 验收

- 至少 5 个真实 create_plan 场景可生成结构化计划；
- 至少 3 个 replan 场景保持连续性；
- Prompt/model/budget 写入 config snapshot 和 Trace；
- 任务非法状态转移返回 409；
- 步骤勾选与验收使用版本锁；验收未通过保持进行中，完成任务后取消步骤会恢复进行中并使验收失效；
- Review 能给出 suggested_replan 和 next_plan_action；
- 用户确认后创建 continue/adjust 新 Run；
- 来源计划只在归档与创建新计划的事务成功提交后归档；
- 模型超时、格式错、限流、规则失败都有明确 failed/degraded 策略；
- 没有 Tool 时模型不得输出 Tool Call；
- 真实模型测试使用可控小样本，不把网络依赖塞进全部单元测试。
