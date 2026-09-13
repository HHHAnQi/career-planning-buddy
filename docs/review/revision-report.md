# 设计稿一致性审查与修订报告

## 审查结论

本轮直接处理“Agent 设计仍不够完善”的反馈，并同步检查产品、API、数据模型、状态机、SSE、Prompt、Tool、Replay、Eval 和阶段任务书是否能对齐实现。

修订后，设计不再只是节点清单，而是一套可由 Codex 按阶段施工的 Agent Runtime 契约。项目仍为独立实现，不以 ClawAgent 为底座。

## Agent 设计补全

1. 新增 `model-design/agent-runtime/README.md`，定义 Executor、GraphFactory、NodeRunner、Finalizer、Budget、Event、Trace、Snapshot 的职责。
2. 固定 Graph 路由：风险分流、意图路由、上下文、唯一真 Agent、确定性校验、一次受控修复、模板降级和终态持久化。
3. 明确只有 `CareerPlanningAgent` 能自主 Tool Calling；其余节点都是规则、模板或 Service 适配节点。
4. 定义可序列化 Graph State、不可变输入、planning window、证据目录、候选、校验和终态 DTO。
5. 补齐 config/input snapshot，Replay 不再读取已经变化的当前用户数据。
6. 修正模型预算：Stage 2/3 全局 5 次、Stage 4 全局 7 次、online reviewer 8 次；AgentTurn 分阶段为 1/3 次。
7. 格式修复和业务修复分离；业务修复关闭 Tool，修复后只重跑 validator，不重跑 Agent。
8. 新增 Tool 施工规范：可序列化 ModelToolSpec、进程内 RegisteredTool、白名单、Schema、超时、去重、结果压缩、注入防护和 Replay fixture。
9. Stage 2/3 Tool 列表为空，Stage 4 才启用 Memory/RAG/Search 三个只读 Tool。
10. 所有 Run 终态统一由 AgentRunFinalizer 写入；persist 的 step、plan.ready、Run 终态和 terminal event 在同一事务中完成，terminal event 永远最后。
11. EventRecorder 使用原子 next_event_sequence，不使用 `MAX(sequence)+1`。
12. Cancel API 只返回“取消请求已接受”，客户端等待权威 cancelled 终态，不提前伪报成功。
13. quality_reviewer 默认在 Eval/Replay 中离线 shadow，不向已终态 Run 追加事件；online enforce 仅为实验开关。
14. distill_evidence 使用独立 best-effort 记录，不阻塞主链，也不破坏终态事件顺序。

## 业务闭环修复

审查发现旧版同时写了“未来 5 周计划”和“只生成今日 1~3 个任务”，但缺少两者的结构关系，也没有明确不需要调整时如何生成次日任务。本轮统一为：

- 方向层：1~8 周 planning window、overall_direction、weekly_focus；
- 行动层：只展开 planning_date 当天 1~3 个任务；
- Review 后由用户点击生成下一计划；
- `continue` 延续方向和周重点，`adjust` 根据阻碍/时间变化调整；
- completed Plan 仍可作为次日来源；
- 归档来源计划和插入新计划必须在同一事务成功提交；
- `start-next-plan` 由服务端注入 source_review_id/replan_mode，客户端不能伪造。

对应同步修改了 PlanCandidate、ContextBuilder、IntentResult、Validator、Plan/Review 表、API、状态机、用户手册和 Stage 3 任务书。

## 其他部分检查结果

- API：终态 result_kind、next-plan、取消、Replay 和错误语义已对齐；
- 数据模型：补充 planning window、weekly focus、Snapshot、序号分配器、Tool fixture 和 evidence refs；
- SSE：heartbeat 不持久化，terminal event 唯一且最后，支持 Last-Event-ID；
- Prompt：只为确实需要模型的节点创建 Prompt，格式修复/业务修复边界明确；
- Provider：编码助手与运行时模型分离，所有真实请求受预算和 Trace 控制；
- 安全：外部 evidence 不可信、Tool 只读、快照/fixture 脱敏、高风险分支不进入规划；
- Eval：增加 horizon、planning_date、continue/adjust 连续性、Tool policy、snapshot/replay 检查；
- 实施顺序：Stage 2 先用 Mock 跑通 Runtime，Stage 3 接真实模型和反馈闭环，Stage 4 再开 Tool，Stage 5 做离线 Reviewer/Replay/Eval。

## 编码前仍需团队确认

- 实际 LLM、单次成本和输出 Token 上限；
- Search/Embedding Provider；
- Eval 阈值和 30 条真实 Case；
- Snapshot、Trace、Tool Fixture 的保留期；
- 高风险资源覆盖地区；
- Demo 部署平台和域名。

这些事项不阻塞 Stage 0~2，可先使用 Mock 和配置接口。

## 最终静态校验

- Markdown 文件：120 个；
- Markdown 总行数：9675 行；
- 内部相对链接：0 个失效；
- JSON 示例：31 个，全部可解析；
- fenced code block：全部闭合；
- 12 个 Agent 节点/增强能力 spec 链接全部存在；
- 权威文档不存在旧版复盘接受接口、6 次旧预算、8 次旧 Tool 预算或 repair 重跑 Agent 的冲突；
- 与上一 Codex 版相比：新增 3 个目录/文档入口，修改 71 个文件。

本包仍是设计文档，不包含业务代码，因此不声称应用编译、迁移或自动化测试已通过。
