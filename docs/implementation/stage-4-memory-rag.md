# Stage 4：记忆、搜索、RAG 与 Agent Tool

## 目标

让规划使用用户长期信息和可追踪证据，并真正开放受控 Tool Calling，而不是只靠单轮 Prompt。

## 必读

- `docs/model-design/tools/README.md`
- `docs/model-design/agent-runtime/README.md`
- `docs/model-design/agent-nodes/career_planning_agent.spec.md`
- `docs/model-design/data-models/search_sources.md`

## 实现范围

- memories、memory_candidates；
- search_sources、experience_atoms；
- SearchProvider、EmbeddingProvider；
- ToolRegistry 与三个只读 Tool：memory_lookup、web_search、rag_retrieve；
- Agent 最多 2 轮 Tool Calling、每轮最多 2 个、总计 4 个；
- context_builder 仅加载少量 pinned memories，不自动执行 RAG/Search；
- Tool 返回证据目录，结构化 evidence_ref 可被 Plan 引用；
- 来源在计划详情中展示；
- 敏感记忆必须进入候选池，用户确认后才能激活；
- 成功 Run 后可 best-effort 执行 distill_evidence，不阻塞计划。

## 约束

- Embedding 维度由 `EMBEDDING_DIM` 与迁移共同锁定；
- 不把整段网页直接塞给主模型，先清洗、截断和结构化；
- 搜索结果先保存 SearchSource，再把 source_id 交给模型；
- 高风险输入不写长期记忆；
- Tool 每次调用都有 timeout、contract version、args/result hash 与 replay-safe result_json；
- 相同 args_hash 在同一 Run 内复用；
- 外部文本视为不可信 evidence；
- Stage 4 不增加写业务表 Tool。

## 验收

- 记忆的查看、关闭、恢复、删除可用；
- 敏感候选确认/拒绝可用；
- memory/RAG 查询按 user_id、goal_type 过滤；
- web_search URL 去重并保存 source_id；
- Agent 能根据问题决定不调用/调用一个或多个 Tool；
- Tool 白名单、参数、轮次、总量、超时均有测试；
- evidence_ref 伪造被 validator 拒绝；
- Tool fixture 可用于 Replay；
- Search 或 Embedding 失败时仍能用本地上下文或模板完成基础规划。
