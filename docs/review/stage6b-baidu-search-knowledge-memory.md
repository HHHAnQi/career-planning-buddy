## 三层记忆架构与当前状态

### L1 Working Memory — 已完成

负责单次 Run 的当前上下文：

- 当前用户请求；
- Profile；
- 当前 Plan；
- 最近 Task 和 Review；
- 压缩后的历史摘要；
- 本次检索出的 Memory 和 Evidence；
- PlanningContext 与 RunInputSnapshot。

Stage 6A 已完成上下文压缩、Prompt Isolation 和 Snapshot，
本轮不得重写。

### L2 Personal Episodic Memory — 已完成

负责当前用户的跨 Run 私有记忆：

Review
→ MemoryCandidate
→ 用户确认或拒绝
→ active Memory
→ BGE Embedding
→ pgvector semantic retrieval
→ 下一次 PlanningContext
→ Plan evidence_refs

Stage 6A 已完成代码和自动测试。
本轮仅做回归保护，不重写该链路。

### L3 Semantic Knowledge Memory — 本轮完成

负责跨 Run、可复用、非用户私有的通用求职知识：

真实百度 Web Search
→ SearchSource
→ ExperienceAtomCandidate
→ 开发者审核
→ ExperienceAtom
→ BGE Embedding
→ pgvector retrieval
→ EvidenceCatalog
→ Plan evidence_refs

本轮只补齐 L3，严禁将 Personal Memory 与
ExperienceAtom 合并为同一种数据。
请基于当前分支
feat/stage6b-baidu-search-knowledge-memory
实施 Stage 6B：百度真实搜索接入与第三层语义知识记忆闭环。

当前 Stage 6A 已完成：
- Working Memory 上下文压缩与 Prompt Isolation；
- Review → MemoryCandidate → 用户确认 → Personal Memory；
- Memory Semantic Retrieval；
- Stage 5 Eval 30/30；
- Stage 6A Eval 12/12。

本轮只补充第三层 Semantic Knowledge Memory，不重写 Stage 6A。

开始前阅读：

1. AGENTS.md
2. AGENTS.zh-CN.md
3. CODEX-CODING-GUIDE.md
4. backend/app/core/config.py
5. backend/app/providers/search.py
6. backend/app/tools/executors.py
7. backend/app/tools/registry.py
8. backend/app/models/evidence.py
9. backend/app/repositories/evidence.py
10. backend/app/agent/graph.py
11. backend/app/agent/nodes.py
12. docs/model-design/agent-nodes/distill_evidence.spec.md
13. Stage 6A 相关实现和验收报告

先静态检查现有代码，再给出简短实施计划，然后直接开发。

==================================================
一、真实百度搜索 Provider
==================================================

1. 保留 MockSearchProvider。

测试、Eval、普通 CI 默认继续：

SEARCH_PROVIDER=mock

本地真实运行：

SEARCH_PROVIDER=baidu

严禁删除 Mock Provider，严禁在百度搜索失败时静默返回 Mock 数据。

2. 扩展 Settings：

- search_provider: Literal["mock", "baidu"]
- baidu_search_api_key: SecretStr | None
- baidu_search_base_url
- baidu_search_edition: Literal["lite", "standard"]
- baidu_search_max_results
- baidu_search_timeout_seconds

SEARCH_PROVIDER=baidu 时如果缺少 BAIDU_SEARCH_API_KEY，
应用必须启动失败，不能静默切回 mock。

空字符串应被标准化为 None。

3. 新增 BaiduSearchProvider。

接口：

POST
https://qianfan.baidubce.com/v2/ai_search/web_search

Header：

X-Appbuilder-Authorization: Bearer <API Key>
Content-Type: application/json

请求体：

{
  "messages": [
    {
      "role": "user",
      "content": "<query>"
    }
  ],
  "edition": "lite",
  "search_source": "baidu_search_v2",
  "resource_type_filter": [
    {
      "type": "web",
      "top_k": 5
    }
  ]
}

严格以百度官方文档为准，不猜测不存在的字段。

4. 搜索 Query 处理。

百度搜索 Query 有长度限制。

新增纯函数，例如：

compact_baidu_search_query(
    query: str,
    *,
    max_weighted_chars: int = 72,
) -> str

要求：

- 中文字符权重 2；
- ASCII 字符权重 1；
- 优先保留岗位、技术、年份、目标等关键词；
- 删除 Prompt 标签、控制字符和过长上下文；
- 不能为空；
- 不使用 LLM 做 Query 改写，第一版保持确定性；
- Trace 只记录 query hash 和长度，不记录完整敏感 Query。

5. 响应映射。

从官方 references 映射为现有 SearchResultItem：

- url
- title
- snippet/content
- source_type
- reliability
- retrieved_at

不得凭空信任百度结果。

source_type 使用确定性域名规则：

- 政府、高校、厂商官方文档、官方招聘页：official
- 招聘平台：job_board
- 博客平台：blog
- 论坛/社区：community
- 其他：other

reliability 使用可测试的确定性策略，不使用 LLM：

- official：0.90
- job_board：0.75
- blog：0.60
- community：0.45
- other：0.50

该分数只表示来源类型先验，不表示内容事实一定正确。

6. 错误处理。

必须区分：

- 认证失败；
- 权限不足；
- 429 限流；
- 请求超时；
- 5xx；
- 响应 Schema 异常；
- 网络异常。

统一转换为项目现有 Provider/Tool 错误，不泄露 Key。

真实百度搜索失败时：

- web_search Tool 标记 failed/degraded；
- 记录结构化错误码；
- 基础 Plan 仍可继续；
- 严禁返回 Mock fixture；
- 严禁把异常原文和请求 Header写入 Trace。

7. Provider Factory。

build_search_provider(settings) 根据配置返回：

- MockSearchProvider
- BaiduSearchProvider

不要在 Provider 内部直接读取全局 Settings。
通过构造函数注入配置。

==================================================
二、SearchSource 正式化
==================================================

8. 保留现有 URL 规范化和 UTM 去除，补充：

- hostname 小写；
- fragment 去除；
- 默认端口去除；
- query 参数稳定排序；
- 百度跳转链接尽可能解析为真实目标 URL；
- URL hash；
- 内容 hash；
- 同一 Run URL 去重。

9. SearchSource 至少保存：

- run_id
- canonical_url
- title
- snippet/content
- source_type
- reliability
- provider=baidu
- retrieved_at
- content_hash
- provider_request_id（如官方响应提供）
- published_at（如官方响应提供）

如现有 Schema 不足，创建最小 Alembic 迁移。

不得保存 API Key、完整请求 Header 或不必要的用户隐私。

10. 当前 Run 可以立即使用 SearchSource 作为 evidence。

即使尚未沉淀为长期 ExperienceAtom，
搜索结果也可以在当前 Plan 中通过：

evidence_refs(kind="search_source", id=...)

被引用。

==================================================
三、第三层 Semantic Knowledge Memory
==================================================

11. 明确三层架构：

L1 Working Memory
- PlanningContext、当前 Run、压缩历史
- Stage 6A 已完成

L2 Personal Episodic Memory
- MemoryCandidate、Memory
- 用户私有、需用户确认
- Stage 6A 已完成

L3 Semantic Knowledge Memory
- SearchSource
- ExperienceAtomCandidate
- ExperienceAtom
- 跨 Run 可复用的通用求职知识
- 本轮完成

不要把 Personal Memory 和 ExperienceAtom 混成同一张表。

12. 新增 ExperienceAtomCandidate。

建议状态：

- pending
- approved
- rejected
- expired

建议字段：

- id
- goal_type
- title
- content
- source_ids
- evidence_excerpt
- confidence
- content_hash
- status
- proposed_by_run_id
- approved_atom_id
- expires_at
- created_at
- decided_at

要求：

- 每条 Candidate 必须引用至少一个 SearchSource；
- evidence_excerpt 必须能在对应 SearchSource 内容中找到；
- content 必须是单一、可复用、原子化的知识；
- 不允许包含用户私人信息；
- 不允许无来源生成；
- content hash 用于跨 Run 去重。

创建对应 ORM、Repository、Service、Schema 和 Alembic 迁移。

13. 实现 distill_evidence。

新增真实代码实现现有 distill_evidence spec。

输入：

- goal_type
- 用户的搜索目标
- 当前 Run 的 SearchSource 列表
- 每个来源的 title、url、snippet/content、reliability

输出严格 Schema：

{
  "candidates": [
    {
      "title": "...",
      "content": "...",
      "source_ids": ["uuid"],
      "evidence_excerpt": "...",
      "confidence": 0.0
    }
  ]
}

规则：

- 每轮最多生成 3 条；
- 每条只表达一个事实或建议；
- content 不超过 300 字；
- evidence_excerpt 不超过 300 字；
- 不输出详细 Chain-of-Thought；
- 必须经过 Pydantic Schema 验证；
- 失败时允许一次 format repair；
- 提炼失败不能导致基础 Plan 失败；
- 不自动将 Candidate 变成正式 ExperienceAtom。

14. Candidate 审核方式。

当前系统没有管理员权限模型，本轮不要开放“任何 Guest 用户都能批准全局知识”的公共 API。

实现一个开发者维护脚本，例如：

python -m scripts.review_experience_candidates list
python -m scripts.review_experience_candidates approve <candidate_id>
python -m scripts.review_experience_candidates reject <candidate_id>

批准时：

- 再次校验来源存在；
- 校验 evidence_excerpt；
- 调用现有 EmbeddingProvider；
- 创建带 1024 维向量的 ExperienceAtom；
- Candidate 状态改为 approved；
- 写 approved_atom_id；
- 保证幂等；
- 不重复创建相同 content_hash 的 Atom。

脚本不得打印 API Key 或完整敏感配置。

暂时不新增 Admin 前端页面。

15. ExperienceAtom Retrieval。

复用现有 rag_retrieve：

- 仅检索 is_active=true；
- 按 goal_type 过滤；
- BGE + pgvector cosine；
- 设置最低相似度阈值；
- 最多返回 5 条；
- 每条保留来源 IDs 和 evidence excerpt；
- 进入 EvidenceCatalog；
- 最终 Plan 可通过 evidence_refs 引用 ExperienceAtom。

16. 检索优先级。

创建/重规划时：

第一步：读取 L1 Working Memory；
第二步：读取 L2 Personal Memory；
第三步：调用 rag_retrieve 查询已有 ExperienceAtom；
第四步：只有以下情况才允许 web_search：
- 用户明确要求搜索；
- 请求包含“最新、今年、当前市场、岗位趋势、招聘要求”等时效性标记；
- 或内部 ExperienceAtom 检索为空/低于阈值。

不要每次规划都调用百度搜索。

==================================================
四、测试与真实验证
==================================================

17. 自动测试必须使用 MockSearchProvider。

新增测试：

- build_search_provider 正确选择 mock/baidu；
- baidu 模式缺少 Key 启动失败；
- API Key 不进入 repr/log/trace；
- Query 长度裁剪；
- 百度响应映射；
- URL 规范化；
- 结果去重；
- 401/403/429/timeout/5xx；
- 百度失败不返回 Mock；
- SearchSource 持久化；
- Candidate 必须绑定来源；
- evidence_excerpt 可验证；
- Candidate 去重；
- approve 后创建 Embedding 和 ExperienceAtom；
- reject 后不创建；
- Guest 用户不能通过公共 API 审批全局 Atom；
- rag_retrieve 能检索批准后的 Atom；
- Plan evidence_refs 合法引用；
- 用户私人 Memory 不会写进 ExperienceAtom；
- 原 Stage 5、Stage 6A 全部不回退。

18. 新增 Stage 6B Eval 数据集，至少 12 条：

- 最新岗位信息触发 web_search；
- 普通规划不触发 web_search；
- 内部 RAG 足够时不触发 web_search；
- 百度搜索结果被保存；
- 搜索失败仍生成基础计划；
- 相同 URL 去重；
- 无来源不能生成 Candidate；
- 有来源生成 Candidate；
- 未批准 Candidate 不参与 RAG；
- 批准后参与 RAG；
- 无关 Atom 不命中；
- evidence_refs 正确。

19. 真实百度验证。

不打印、不读取展示 .env 内容。

使用本地 Settings 正常加载环境变量，真实执行：

- 一次中文求职岗位搜索；
- 一次时效性搜索；
- 一次重复 URL 去重；
- 一次 SearchSource 保存；
- 一次 Candidate 提炼；
- 手动批准一个 Candidate；
- BGE 生成向量；
- 下一次规划通过 rag_retrieve 命中该 Atom；
- 最终 Plan 引用该 ExperienceAtom。

记录：

- 百度 request_id；
- 返回结果数；
- 去重后数量；
- SearchSource 数量；
- Candidate 数量；
- ExperienceAtom ID；
- 检索 score；
- Plan evidence_refs；
- 请求耗时。

不得输出 API Key、Authorization Header 或完整 .env。

==================================================
五、本轮明确不做
==================================================

- 不删除 Mock Provider；
- 不在普通 CI 调真实百度 API；
- 不实现网页任意抓取器；
- 不请求用户提供账号密码或 Cookie；
- 不绕过 robots.txt、登录或反爬；
- 不把搜索摘要直接自动晋升为 ExperienceAtom；
- 不开放 Guest 用户审批全局知识；
- 不实现多 Agent；
- 不实现 HyDE；
- 不实现 Reranker；
- 不实现 BM25 Hybrid；
- 不实现 Redis 或多 Worker；
- 不重写 Stage 6A；
- 不读取、输出或提交 .env；
- 不修改 docs/design-input；
- 不执行 git push。

==================================================
六、最终验收
==================================================

完成后执行：

docker compose config
docker compose up -d postgres
docker compose ps

cd backend
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m alembic current
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m mypy app tests scripts
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m scripts.run_eval
.\.venv\Scripts\python -m evals.stage6_runner
.\.venv\Scripts\python -m evals.stage6b_runner

cd ..\frontend
npm test
npm run build

cd ..
.\scripts\check.ps1
git diff --check
git status --short

如 Windows Codex 沙箱阻止子进程，应分别运行对应命令，
不得把沙箱 EPERM 误报为代码通过。

最后汇报：

- 修改文件；
- 新增迁移；
- Provider 设计；
- 三层记忆链路；
- 自动测试结果；
- 真实百度测试结果；
- 未验证项；
- 未实现项；
- git status。

不要执行 git push。