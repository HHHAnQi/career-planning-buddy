# Career Planning Buddy v1.0 最终 Gap Analysis

> 日期：2026-08-09  
> 审查基线：当前 checkout `0de0292484176752b528c021129bde0724d4157e` 及工作区中的 hardening 任务书  
> 事实来源：当前代码、配置模板、测试和可执行命令；不采用旧 gap-analysis 或 handoff 的历史结论  
> 目标：完成 P0、P1 和低风险 P2 后，判断项目是否达到可完整演示的 v1.0 Release Candidate

## 1. 审查边界与当前事实

- 当前分支为 `feat/stage6-memory-upgrade`，相对其远端跟踪分支领先 40 个提交；本轮不 merge、rebase、commit 或 push。
- `.env` 被 `.gitignore` 忽略且未被 Git 跟踪。本轮不读取 `.env`；允许读取无真实密钥的 `.env.example`。
- `docs/design-input` 不在本轮修改范围。
- 当前是 FastAPI + React SPA + PostgreSQL/pgvector 的单体、单 Worker 系统。本轮不增加多 Agent、MCP、Redis、Celery、微服务或新的 RAG 基础设施。
- 数据库当前 Alembic 单 head 为 `20260815_0018`。如版本身份需要新增持久化列，只通过一个最小 Alembic 迁移完成。
- 已存在并保留的能力：产品主链、唯一终态事件、L1/L2/L3 三层记忆、真实 Baidu Search、Provider audit/fixture、Eval V2、Pairwise Judge/Calibration。

## 2. 本轮实施清单

### FG-P0-01：Eval 取消可能遗留非终态 Experiment/Trial

- **优先级**：P0
- **代码证据**：`backend/app/agent/eval_executor.py` 的 `request_cancel()` 对后台 Task 调用 `task.cancel()`；`_execute()` 只捕获 `Exception`。Python 3.12 的 `asyncio.CancelledError` 不会进入该分支。`backend/evals/v2/experiment_runner.py` 同样只捕获 `Exception`，且没有把取消请求收敛为 Experiment/Trial 的持久终态。文件中的 `_cancel_was_requested()` 没有调用点。
- **影响**：开发者执行 cancel 后，数据库可能继续显示 `running`，直到进程重启恢复；破坏状态机和演示可信度。
- **修改方案**：在 Eval Service 中增加幂等取消收敛用例；Executor 显式捕获取消并调用该用例；所有 pending/running Trial 写 `cancelled` 与稳定错误码，Experiment 只写一次 `cancelled`。
- **修改文件**：`backend/app/agent/eval_executor.py`、`backend/app/services/evals.py`、相关生命周期测试。
- **测试方法**：取消运行中的后台 Eval，断言 Experiment 与全部非终态 Trial 均收敛、重复取消幂等。
- **本轮是否实施**：是。

### FG-P1-01：Eval Experiment 的版本身份不可复现

- **优先级**：P1
- **代码证据**：`backend/app/api/evals.py::_build_config` 与 `backend/evals/v2/__main__.py::_build_config` 写死 `git_commit="0000000"`、`career-plan-v1`、`tool-contract-v1`、`context-v1`、`memory-v1`；而 `SnapshotService` 已使用 `*_stage6_context_v1` Prompt。
- **影响**：Experiment 声称冻结版本，但身份与真实 Runtime 不一致，无法可靠复现或比较。
- **修改方案**：建立唯一 canonical runtime identity；`APP_GIT_COMMIT` 显式值优先，本地 best-effort Git SHA，失败为 `unknown`；API、CLI 与 Snapshot 共用 Prompt/Graph/Context/Memory/Search/Tool/Eval 版本来源；新 Experiment 持久化完整身份，旧数据保持可读。
- **修改文件**：新增 `backend/app/runtime/versioning.py`；修改 Settings、Snapshot、Eval API/CLI/contract/model/service；新增最小 Alembic `0019`；更新 CI/Compose/模板和测试。
- **数据库变化**：为 `eval_experiments` 增加 `feature_stage`、`search_version`、`eval_harness_version`，历史行使用诚实的 legacy 默认值，不改写旧 Snapshot。
- **API 变化**：Eval 列表/状态响应透出版本身份、baseline/variant 信息。
- **测试方法**：显式 SHA 优先、Git 不可用返回 `unknown`、API/CLI identity 相同、Prompt 与 Snapshot 一致、旧 Experiment 查询兼容、迁移 upgrade/downgrade。
- **本轮是否实施**：是。

### FG-P1-02：新 Run 的 Stage/Graph 元数据停留在 Stage 5

- **优先级**：P1
- **代码证据**：`Settings.agent_graph_version` 默认 `stage5-v1`、`agent_feature_stage` 上限为 5；`.env.example` 与 `compose.yaml` 也固定为 Stage 5；`RuntimeConfigSnapshot.feature_stage` 只允许 3/4/5。
- **影响**：新 Run Snapshot 与当前已运行的 Stage 6A/6B 三层记忆、Baidu Search、Stage 6 Prompt 不一致。
- **修改方案**：新运行默认 `stage6b-v1`、feature stage 6；Schema 继续允许 3/4/5 以读取历史 Snapshot。
- **修改文件**：Settings、Snapshot Schema、模板、Compose、测试与文档。
- **测试方法**：新 Snapshot 为 Stage 6，历史 Stage 5 Snapshot 仍可验证。
- **本轮是否实施**：是。

### FG-P1-03：Live Eval 无受控 retry/backoff/pacing

- **优先级**：P1
- **代码证据**：`TrialRunner._build_executor()` 的 live 分支只有 Audit wrapper，注释仍写未来的 RetryingProvider；`ProviderCall.retry_attempt` 在 live 物理重试中始终为 0；真实验证文档已记录 timeout/rate limit。
- **影响**：短暂 429、timeout、5xx 被直接计为失败；报告混淆 Provider 可靠性和 Agent 质量；批量请求容易进一步触发限流。
- **修改方案**：新增 Eval-only retry/pacing wrapper，顺序为 `Retry/Pacing(Audit(real provider))`，确保每次物理调用单独审计；只重试 429、timeout、网络瞬断和 5xx；指数退避、受限 jitter、Retry-After、并发上限、跨调用 pacing、deadline 与 cancellation 均生效；耗尽时使用稳定错误码。
- **修改文件**：Settings、Provider error 元数据、LLM/Search provider、Provider audit、TrialRunner、新 retry 模块、报告分类与测试。
- **API/数据库变化**：无新表；复用 `provider_calls.retry_attempt`。报告增加稳定 failure breakdown。
- **测试方法**：429/timeout/5xx 后成功、401/Schema 不重试、最大次数、Retry-After、每次 audit、取消、deadline、pacing/concurrency，全部 fake provider。
- **本轮是否实施**：是。

### FG-P1-04：README 与真实系统不一致

- **优先级**：P1
- **代码证据**：README 声称仅 Stage 6A、仅 Mock Search、真实 Web Search 不在范围，也没有说明 Eval V2/Pairwise/Calibration 和三层记忆完整边界。
- **影响**：新开发者和面试演示者无法从唯一入口理解或正确启动当前系统。
- **修改方案**：按当前代码重写定位、架构、三层记忆、Provider 模式、Mock/本地/Compose 启动、Eval V2、限制与诚实质量声明。
- **修改文件**：`README.md`。
- **测试方法**：命令与 Settings/Compose/CLI `--help` 逐项核对，文档链接可达。
- **本轮是否实施**：是。

### FG-P1-05：Agent Node 与 Eval 历史文档冒充当前状态

- **优先级**：P1
- **代码证据**：Agent Node 索引仍称 `distill_evidence` 未实现，但 `AgentRunExecutor._distill_evidence_best_effort()` 已调用 `ExperienceAtomService.distill_run()`；`eval-harness-v2.md` 顶部仍称 TrialRunner 等属于未来 PR；`pr-9c-handoff.md` 顶部状态已过期。
- **影响**：L2 Personal Memory 与 L3 Knowledge Memory 容易被误解，历史 TODO 会被当成当前缺口。
- **修改方案**：修正节点索引，明确两个 distiller 的边界；历史文档顶部标记 Historical/Superseded 并指向当前总览；保留有价值的历史证据。
- **修改文件**：节点索引、两份 Eval 实施记录、handoff 顶部、最终系统总览。
- **测试方法**：文档描述逐项映射当前类/Service/路由。
- **本轮是否实施**：是。

### FG-P1-06：Docker 无法显式 opt-in Baidu Search

- **优先级**：P1
- **代码证据**：`compose.yaml` 把 `SEARCH_PROVIDER` 固定为 `mock`，未传 Baidu 配置。
- **影响**：Docker 用户即使提供合法配置也无法选择真实 Search；README 的部署路径不完整。
- **修改方案**：保留 mock 默认，加入独立的 `COMPOSE_SEARCH_PROVIDER` 和 `COMPOSE_BAIDU_SEARCH_*` 变量；不向 frontend 暴露任何 Secret。
- **修改文件**：`compose.yaml`、`.env.example`、README。
- **测试方法**：`docker compose config` 验证默认 mock 和显式 opt-in 展开；检查 frontend build args 不含 Secret。
- **本轮是否实施**：是。

### FG-P1-07：Eval V2 后端能力没有最小开发者前端入口

- **优先级**：P1
- **代码证据**：前端 Router 只有 `/dev/runs`，没有 `/dev/evals`；后端已有 Eval run 和 calibration API。
- **影响**：项目最重要的评测能力无法在演示中被合理访问。
- **修改方案**：新增小型 `/dev/evals`：列表、创建 mock/fixture 小实验、状态/进度、取消、报告摘要、trial failure/token/provider 摘要，以及最新 calibration 的 `diagnostic_only/gate_eligible`；不提供浏览器 live 一键付费入口。
- **修改文件**：新增前端 Eval API、页面与测试；更新 Router/My 页面；必要时扩展现有列表响应字段。
- **测试方法**：页面测试覆盖统一 JWT、创建、选择、进度、失败分类、取消和 calibration 404→diagnostic。
- **本轮是否实施**：是。

### FG-P1-08：Developer 前端重复维护 JWT

- **优先级**：P1
- **代码证据**：公共 client 使用 `cpb_access_token`；`DeveloperRunsPage` 与 `api/dev.ts` 另用 `career_buddy_dev_token` 并自行拼 Authorization。
- **影响**：登录用户仍需粘贴 Token，重复认证逻辑且容易出现不一致错误处理。
- **修改方案**：Dev API 全部复用 `apiRequest`；删除手工 Token 输入和本地存储；后端 `require_dev` 保持唯一安全边界。
- **修改文件**：`frontend/src/api/dev.ts`、DeveloperRunsPage 及测试。
- **测试方法**：断言请求使用公共 `cpb_access_token`，页面不再出现 Token 输入，401/403 使用统一错误。
- **本轮是否实施**：是。

### FG-P1-09：Canonical CI/check 未执行 Eval V2 端到端 smoke

- **优先级**：P1
- **代码证据**：CI 与 `scripts/check.ps1` 只显式运行 legacy `scripts.run_eval --no-persist`；没有执行 `python -m evals.v2 run`。
- **影响**：Case→Experiment→Trial→Grade→Report 的公开 CLI 入口可在单测通过时仍整体失效。
- **修改方案**：使用现有 `runtime-smoke` 数据集的 1 个 case、mock provider、1 trial，加入 CI 和 check；不调用付费 Provider/BGE/Baidu。
- **修改文件**：CI、PowerShell/Shell check 脚本，必要时增加小 smoke 专用 case 参数说明。
- **测试方法**：先以 `python -m evals.v2 --help` 确认参数，再执行真实 CLI 并断言 JSON report 完成且有 score。
- **本轮是否实施**：是。

### FG-P1-10：Eval API 契约没有完整表达已支持的实验身份

- **优先级**：P1
- **代码证据**：`EvalRunCreateRequest.agent_variant` 已存在，但 `api/evals.py::_build_config()` 没有接收或写入它；传入 `baseline_experiment_id` 时仍固定 `variant_role="baseline"`，会触发 contract 冲突；列表响应缺 dataset_version、baseline/variant 和版本字段。
- **影响**：前端无法创建合法 candidate，对比身份不可见，前后端契约不能支撑最小 Eval Console。
- **修改方案**：正确映射 candidate/baseline 与 `agent_variant`；扩展只读响应；由 OpenAPI snapshot 测试生成并审查变化。
- **修改文件**：Eval API/schema/tests、OpenAPI snapshot、前端类型/API。
- **测试方法**：API baseline/candidate happy path、非法 baseline、agent variant 持久化、OpenAPI snapshot、前端 TypeScript build。
- **本轮是否实施**：是。

### FG-P1-11：三层记忆与百度搜索需要保持回归门禁

- **优先级**：P1
- **代码证据**：当前已有 `test_stage6_memory_selection.py`、`test_stage6_context_rendering.py`、`test_stage6b_knowledge_memory.py`、`test_validate_baidu_search.py` 等；代码中 L2 按 user/active/consent 过滤，L3 经 SearchSource→candidate→developer approval，Baidu build 失败不回退 Mock。
- **影响**：版本/Provider/Eval 改动若误接线，可能破坏三层边界或把真实搜索降级成 fixture。
- **修改方案**：不重建基础设施；保留并在 canonical check/最终验证中显式执行相关回归，补充本轮触及的 hostname 与版本 Snapshot 测试。
- **修改文件**：测试/CI 文档；生产实现仅做已证实的低风险修复。
- **测试方法**：L2 用户隔离/确认边界、L3 审批、Baidu 不 fallback、RAG evidence_refs、Stage 6 Snapshot。
- **本轮是否实施**：是。

### FG-P2-01：Baidu 来源域名分类可被相似域名误判

- **优先级**：P2（低风险）
- **代码证据**：`backend/app/providers/search.py::classify_source` 对 hostname 使用任意 substring，例如 `"zhipin.com" in host`，会把 `zhipin.com.attacker.example` 误判为 job board；`.gov.cn` 形式也不统一处理根域/子域。
- **影响**：只影响来源类型和可靠性先验，但会降低 evidence 展示可信度。
- **修改方案**：新增安全 `host_matches`，只接受根域或点分隔子域；lowercase、IDNA、malformed URL fail closed；对 `jobs.`/`blog.` 使用明确标签前缀规则。
- **修改文件**：Search Provider 与单测。
- **测试方法**：根域、子域、大小写/IDNA、恶意相似域、非法 URL。
- **本轮是否实施**：是。

### FG-P2-02：未挂载的 HomePage 是死代码

- **优先级**：P2（低风险）
- **代码证据**：`HomePage.tsx` 与测试存在，但 Router 根路径直接跳转 `/today`，没有任何 import/route 引用。
- **影响**：保留无效页面和误导测试；其中还链接到旧的开发者入口叙述。
- **修改方案**：删除 HomePage 与其测试，不重新设计 Landing Page。
- **修改文件**：两个前端文件。
- **测试方法**：`rg HomePage frontend/src` 无引用，前端测试/build 通过，首次访问仍走 Guest Login→Today。
- **本轮是否实施**：是。

### FG-P2-03：部分 stale comment/docstring 描述旧实现

- **优先级**：P2（低风险）
- **代码证据**：TrialRunner live docstring 声称 live 无 Audit，但后续代码已安装 Audit；EvalRunnerExecutor 声称会轮询取消，实际没有调用；Agent Runtime 文档把 EvidenceService 作为当前实现名称。
- **影响**：维护者会按错误 wrapper 顺序或取消语义修改代码。
- **修改方案**：只修本轮确认的 stale comment，不做无关文案重排。
- **修改文件**：对应代码 docstring 与规范。
- **测试方法**：注释与最终调用结构逐项核对。
- **本轮是否实施**：是。

## 3. 本轮明确不实施的 P3

| 项目 | 原因 |
|---|---|
| 多 Worker、Redis/Celery、分布式恢复 | 当前明确是单 Worker Release Candidate；会改变架构基线，不是 v1 收口所需。 |
| Kubernetes、微服务拆分 | 对单机求职作品没有当前阻塞价值。 |
| 网页全文 crawler | Baidu Search 已提供受控摘要和来源；crawler 带来版权、robots 与安全范围扩张。 |
| Hybrid RAG、BM25、Reranker、HyDE | 当前 pgvector 三层记忆闭环已有回归；缺少真实质量证据支持扩张。 |
| 大规模真实 Eval | 会产生费用且本轮目标是稳定小 smoke 和可复现 harness。 |
| 伪造或自动生成 Human Calibration 标签 | 当前人工样本不足必须保持 `diagnostic_only`，不能制造 gate 通过。 |
| Admin 大后台 | 最小 Dev Trace/Eval Console 足够演示和排障。 |
| Local BGE 完整容器化/GPU | 环境成本高；Compose 安全默认 mock，本地 backend 是真实 Provider 推荐路径。 |

## 4. 计划验收

1. 单元/契约：新增 runtime identity、retry/pacing、Eval cancel、API identity、Baidu hostname、Dev UI tests。
2. 数据库：Alembic 单 head、upgrade/current；迁移和历史 Experiment 兼容测试。
3. 后端：Ruff、Mypy、全量 Pytest、legacy Stage 5/6 Eval、Eval V2 deterministic smoke。
4. 前端：Vitest、TypeScript/Vite build。
5. 部署：`docker compose config`；Docker 可用时启动 PostgreSQL 并跑完整 check。
6. 安全/范围：`.env` 未跟踪、无 Secret 输出、SSE Authorization header、Dev API role、`docs/design-input` 无 diff。
7. 真实 E2E：仅通过 Settings 正常加载已配置 Provider，先检查配置可用性布尔状态；可用时执行最小真实链路和 live Eval，不可用时准确记录环境阻塞，绝不替换为 Mock 冒充成功。

## 5. 完成标准

- P0 全部关闭；P1 全部关闭或有可复查的外部环境阻塞；只实施低风险 P2。
- 新 Experiment 版本身份与新 Run Snapshot 来自同一个 canonical identity，旧数据可读。
- live Eval 的每次物理重试可审计、可取消、有 deadline/并发/pacing 上限，报告不混淆失败类型。
- `/dev/runs` 与 `/dev/evals` 使用统一登录 Token，后端仍强制 dev role。
- README 与 current-system-overview 能独立指导启动、使用和解释系统。
- 最终 verification 只依据本轮真实命令和 E2E 结果选择 A/B/C，不以代码规模或历史结论代替证据。

## 6. 本轮实施回填

以上条目是在修改前从真实 checkout 得出的审查结论。实施后状态如下：

| ID | 结果 | 验证摘要 |
|---|---|---|
| FG-P0-01 | 已关闭 | Executor cancellation、持久 cancel marker 与幂等 Service 收敛均有 PostgreSQL 回归测试。 |
| FG-P1-01/02 | 已关闭 | canonical identity 已供 Snapshot/API/CLI 共用；Alembic `20260816_0019` 已从当前旧库实际升级，单 head/current。 |
| FG-P1-03 | 已关闭 | fake Provider 的 429/timeout/5xx、不可重试错误、耗尽、Retry-After、audit、cancel、deadline、pacing/concurrency 测试通过。 |
| FG-P1-04/05/06 | 已关闭 | README、current overview、节点/Eval 历史标记和 Compose Baidu opt-in 已与代码核对。 |
| FG-P1-07/08 | 已关闭 | `/dev/evals` 与 `/dev/runs` 复用普通 JWT；前端 12 tests 与生产 build 通过。 |
| FG-P1-09 | 已关闭 | PowerShell/Shell/CI 已加入一 case Mock V2 smoke；本轮实际 Case→Report 完成。 |
| FG-P1-10 | 已关闭 | candidate/baseline/agent_variant 和完整版本身份已进入 API/OpenAPI/frontend contract。 |
| FG-P1-11 | 已关闭 | 三层记忆、Baidu、不 fallback、L3 approval/RAG/evidence_refs 回归和真实链验证通过。 |
| FG-P2-01/02/03 | 已关闭 | hostname fail-closed、HomePage 删除、确认的 stale doc/comment 已修；相关测试/静态检查通过。 |

外部环境结果不反向改变上述代码缺口状态：真实 Baidu/BGE/L3 链成功；已配置的真实 LLM
在本轮网络条件下返回 `PROVIDER_UNAVAILABLE`；一 case live Eval 如实完成为 deadline runtime
failure，没有切换 Mock。完整命令与最终评级见
[`v1-release-verification-2026-08-09.md`](./v1-release-verification-2026-08-09.md)。
