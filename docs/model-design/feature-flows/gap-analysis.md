# 功能与契约对齐结论

当前设计包已将早期冲突收敛为可编码的独立项目基线。

| 问题 | 收敛结果 |
|---|---|
| 是否基于 ClawAgent | 否，独立项目 |
| 编码助手与运行时模型混淆 | 已分离；Codex 负责开发，运行时由 Provider 配置 |
| 五类空壳 Provider | 收敛为 LLM/Search/Embedding 三类 |
| Agent 只是节点列表、缺 Runtime | 新增 Executor/NodeRunner/Finalizer/Budget/Event/Trace/Snapshot 完整契约 |
| Graph State 过于宽松 | 区分不可变输入、路由、上下文、候选、校验和终态 DTO |
| repair 会重跑整个 Agent | 改为关闭 Tool 的专用修复，一次后重新校验 |
| Agent Tool 边界不清 | Stage 4 只开放 memory/rag/search 三个只读 Tool |
| context_builder 与 Tool 重复检索 | context_builder 只构建基础事实，更多检索由 Agent 按需调用 |
| LLM 总预算与节点预算冲突 | Stage 2/3 全局 5、Stage 4 全局 7、在线 reviewer 时 8；AgentTurn 分阶段为 1/3 |
| Clarification/Safe Response 刷新丢失 | 新增 result_kind/result_payload_json，GET Run 可恢复 |
| Replay 会读取变化后的画像 | 新增 input/config snapshot |
| Replay 无 Tool 原始结果 | tool_calls 增加 replay-safe result_json + contract version |
| SSE heartbeat/终态语义不清 | heartbeat 不持久化；每 Run 唯一 terminal event |
| Plan 状态含义混乱 | generated/active/completed/archived；adopted_at 为时间字段 |
| Review 重复传任务事实 | 删除 task id 列表，由 Service 从数据库计算 |
| 多 Worker 能力夸大 | 明确 MVP 单 Worker和重启限制 |
| 结构化输出无限修复 | 格式修复一次 + 业务修复一次，均受全局预算 |
| 搜索 URL 可由模型生成 | 只能引用本 Run 已保存 source id |
| 硬编码安全资源 | 集中配置并人工审核 |

剩余非阻塞事项：真实模型 smoke、Search/Embedding 供应商、UI 视觉稿、30 条 Eval Case 和实际代码实现。
