# Career Planning Buddy — v1.0 Final Hardening 任务书

> 日期：2026-08-09  
> 基线：以当前工作区最新代码为唯一事实来源，不信任旧 gap-analysis / handoff 的历史结论  
> 目标：将当前项目收口为可启动、可完整使用、可真实演示、可评测、可解释的 v1.0 Release Candidate  
> 原则：**不再增加新的 Stage 编号，不为了“高级”继续堆功能。**

---

# 0. 开始前的执行要求

开始前先阅读：

- `AGENTS.md`
- `AGENTS.zh-CN.md`
- `CODEX-CODING-GUIDE.md`
- `README.md`
- `compose.yaml`
- `.env.example`
- `.github/workflows/ci.yml`
- `scripts/check.ps1`
- `backend/app/`
- `backend/evals/`
- `backend/tests/`
- `frontend/src/`
- `docs/`

然后执行并记录：

```text
git branch --show-current
git status
git log -5 --oneline --decorate
```

要求：

1. 只操作当前 checkout，不自行 merge / rebase / reset。
2. 不执行 `git reset --hard`。
3. 不执行 `git clean -fd`。
4. 不执行 `git commit`。
5. 不执行 `git push`。
6. 不读取、打印、复制、总结 `.env` 内容。
7. 不输出任何 API Key / Authorization Header。
8. 不修改 `docs/design-input`。
9. 数据库结构变化必须使用 Alembic。
10. 不通过删除测试、降低类型检查、扩大忽略范围来“修复”问题。
11. 如果旧文档和代码冲突，以**当前代码**为准，并修正文档。
12. 先做审查并生成 Gap 文档，但**不要停在审查阶段**；随后直接实施 P0 → P1 → 低风险 P2。

最终必须留下工作区供人工 review，不要自动提交。

---

# 1. 当前能力基线：不要重复造已有能力

当前最新代码已经具备以下能力。本轮应先验证，而不是重新实现。

## 1.1 产品主链

当前产品已有：

```text
Guest Login
→ Onboarding / Profile
→ Plan
→ Today Tasks
→ Task 状态变更
→ Review
→ Replan
→ Memories
→ History / Journey
→ Plan Detail / Evidence
→ Developer Run Trace
```

前端已经存在：

- `TodayPage`
- `PlansPage`
- `PlanDetailPage`
- `ReviewsPage`
- `MemoriesPage`
- `MyPage`
- `ProfileSettingsPage`
- `DeveloperRunsPage`

本轮不要无目的新增普通用户页面。

---

## 1.2 Agent Runtime

当前已有：

- 固定 LangGraph workflow；
- Run / Step / ToolCall / Event；
- SSE；
- Snapshot / Replay；
- Budget / deadline；
- format repair；
- business repair；
- deterministic fallback；
- terminal event；
- Provider Protocol；
- OpenAI-compatible LLM；
- Mock Provider；
- DeepSeek 实际运行能力。

本轮不重写 Agent Graph，不因为文件较大就做大规模架构重构。

---

## 1.3 三层记忆

必须保留清晰的三层边界。

### L1 — Working Memory

当前已有：

- 当前 Run；
- 用户请求；
- Profile；
- Plan；
- 最近 Task / Review；
- deterministic history compression；
- PlanningContext；
- RunInputSnapshot；
- Prompt Isolation。

### L2 — Personal Episodic Memory

当前已有：

```text
Review
→ MemoryCandidate
→ 用户确认 / 拒绝
→ Memory
→ Embedding
→ pgvector semantic retrieval
→ PlanningContext
→ Plan evidence_refs
```

并已有：

- pinned priority；
- semantic similarity；
- recency decay；
- context budget；
- last_used_at；
- user isolation；
- fallback；
- Trace / Snapshot。

### L3 — Semantic Knowledge Memory

当前已有：

```text
Baidu Search
→ SearchSource
→ ExperienceAtomCandidate
→ 开发者审核
→ ExperienceAtom
→ BGE Embedding
→ pgvector
→ rag_retrieve
→ EvidenceCatalog
→ Plan evidence_refs
```

并已有真实 `BaiduSearchProvider`。

本轮严禁把 L2 Personal Memory 与 L3 ExperienceAtom 合并成一种数据。

---

## 1.4 Eval Harness V2

当前代码已经不是简单的 30 条 fixture Eval。

已有：

```text
Case
→ Experiment
→ Trial
→ Run
→ Grade
→ Report
```

并已有：

- Eval Run API；
- TrialRunner；
- mock / fixture / live provider mode；
- ProviderCall audit；
- token/error 记录；
- Fixture record/replay；
- deterministic graders；
- Pairwise Judge；
- Pairwise calibration；
- `direct_llm_v1` baseline；
- `full_agent_v1`；
- Eval DB tables / migrations；
- live validation 文档。

本轮不要重新创建第三套 Eval 系统。

---

# 2. 本轮首先生成最新 Gap Analysis

新增：

```text
docs/review/final-gap-analysis-2026-08-09.md
```

所有问题必须来自当前代码证据。

每项至少写：

```text
ID
优先级 P0/P1/P2/P3
代码证据
影响
修改方案
修改文件
测试方法
本轮是否实施
```

优先级定义：

### P0

- 核心用户链路断裂；
- 安全或隐私问题；
- 数据错误；
- 项目无法启动；
- 真实模式伪装成 Mock / Mock 伪装成真实；
- Migration 无法升级。

必须修。

### P1

- 明显影响演示完整性；
- Eval 结果不可复现；
- 代码与文档严重不一致；
- 真实 Provider 评测不可靠；
- 已实现的重要后端能力用户/开发者无法合理使用。

原则上修。

### P2

- 低风险代码质量；
- UX；
- 文档和命名；
- 可维护性。

低风险则修。

### P3

未来扩展，本轮明确不做。

---

# 3. 已确认的重点问题：必须优先复核

下面是对当前最新代码的静态审查发现。

不要盲目照抄结论；先重新打开对应代码确认。如果仍存在，则按本任务要求修复。

---

# 4. P1：Eval 的版本身份与可复现性不真实

## 4.1 当前问题

当前代码中：

```text
backend/app/api/evals.py
backend/evals/v2/__main__.py
```

构造 Experiment Config 时存在类似：

```python
git_commit="0000000"
prompt_version="career-plan-v1"
tool_version="tool-contract-v1"
context_version="context-v1"
memory_version="memory-v1"
```

但真实 Runtime 已经使用 Stage 6 context / memory / RAG / Search，
Snapshot 中实际 prompt version 也已经类似：

```text
openai_compatible_plan_stage6_context_v1
mock_plan_stage6_context_v1
```

这会导致 Eval Harness 虽然宣称 Experiment 冻结配置，但记录的版本身份并不是真实运行版本。

这是本轮高优先级问题。

## 4.2 目标

建立**唯一 canonical runtime/version manifest**。

建议新增类似：

```text
backend/app/runtime/versioning.py
```

或放入已有最合理模块。

需要能生成：

```python
RuntimeVersionIdentity(
    git_commit=...,
    graph_version=...,
    feature_stage=...,
    prompt_versions=...,
    tool_contract_version=...,
    context_version=...,
    memory_version=...,
    search_version=...,
    eval_harness_version=...,
)
```

不要制造重复版本常量。

## 4.3 Git Commit

优先读取显式环境变量，例如：

```text
APP_GIT_COMMIT
```

CI 中传入：

```text
${{ github.sha }}
```

本地开发如果没有显式值：

- 可以 best-effort 获取当前 Git SHA；
- 获取不到时记录 `unknown`；
- **不能继续伪造 `0000000`**。

不要因为获取 Git SHA 失败导致应用无法启动。

## 4.4 兼容要求

- 不修改历史 Experiment 已持久化的数据；
- 不批量改旧 Run Snapshot；
- 新 Experiment 使用真实版本身份；
- 老 Snapshot / Experiment 仍可读取；
- Replay 兼容旧数据。

## 4.5 测试

至少覆盖：

- API 创建 Experiment 使用 canonical identity；
- CLI 创建 Experiment 使用同一 identity；
- CI 显式 SHA 优先；
- 本地无 Git 环境时返回 `unknown`；
- prompt version 与 SnapshotService 一致；
- 历史 Experiment 仍可查询。

---

# 5. P1：Stage / Graph Version 元数据已经落后于真实实现

## 5.1 当前问题

当前 `Settings` 中仍存在类似：

```python
agent_graph_version = "stage5-v1"
agent_feature_stage = 5
```

但实际系统已经包含：

- Stage 6A context / L2 memory；
- Stage 6B Baidu Search / L3 knowledge memory；
- Stage 6 Prompt。

这会造成：

```text
运行能力已经 Stage 6+
但 Snapshot / Experiment 仍显示 Stage 5
```

## 5.2 要求

先判断这些字段是否有历史兼容含义。

如果只是 stale metadata：

- 为**新 Run**升级 canonical graph/runtime version；
- 例如 `stage6b-v1` 或更中性的 `runtime-v1`；
- feature stage 如继续保留，应允许当前真实阶段；
- 更新测试和文档；
- 不修改旧 Run。

如果代码中确有兼容理由必须保留 `stage5-v1`：

- 不强行改；
- 在 Gap 和最终报告中解释原因；
- 但不能让 README 继续误导。

禁止为“看起来更新”做纯 cosmetic rename。

---

# 6. P1：Live Eval 缺少明确的 transient retry / backoff / pacing

## 6.1 当前证据

当前：

```text
backend/evals/v2/trial_runner.py
```

已经有 Provider audit，但代码注释仍说明：

```text
future RetryingProvider wrapper
```

而：

```text
docs/implementation/eval-live-validation-2026-08-08.md
```

已经记录真实 B0/B3 对比受：

- Provider timeout；
- rate limit；

严重影响，并明确提出需要 retry/backoff/pacing。

因此这是已由真实运行暴露的问题，不是理论优化。

## 6.2 本轮目标

增加**Eval-only** 的受控重试和节流层。

不要随意改变普通产品 Agent Runtime 的重试语义。

建议：

```text
Live Provider
→ Retry/Pacing wrapper
→ Audit wrapper
→ Trial
```

或按当前 wrapper 结构选择能保证“每次物理调用都被审计”的正确顺序。

关键要求：

### 可重试

只允许：

- HTTP 429；
- timeout；
- provider 5xx；
- provider temporarily unavailable；
- 明确可恢复的网络瞬断。

### 不可重试

禁止：

- 401；
- 403；
- API Key 配置错误；
- Schema validation；
- business validation；
- prompt contract error；
- 非幂等的内部业务写入错误。

### 策略

提供配置，例如：

```text
EVAL_LIVE_MAX_ATTEMPTS=3
EVAL_LIVE_RETRY_BASE_SECONDS=1
EVAL_LIVE_RETRY_MAX_SECONDS=8
EVAL_LIVE_CONCURRENCY=2
EVAL_LIVE_PACING_SECONDS=0.5
```

要求：

- exponential backoff；
- jitter；
- 如 Provider 返回 Retry-After，优先尊重；
- 每次物理调用记录 `retry_attempt`；
- 仍受 Experiment / Trial deadline 和 Budget 限制；
- 不能无限重试；
- 取消必须能及时停止等待；
- 不允许靠把 timeout 无限调大来解决。

## 6.3 Eval 报告语义

必须区分：

```text
model failure
provider transient failure
provider exhausted-after-retry
cancelled
internal error
```

不要把 Provider rate limit 当成“Agent 质量差”。

## 6.4 测试

全部使用 fake/mock provider：

- 第一次 429 第二次成功；
- timeout 后成功；
- 5xx 后成功；
- 401 不重试；
- schema error 不重试；
- max attempts；
- Retry-After；
- retry_attempt audit；
- cancellation during backoff；
- deadline exhausted；
- pacing/concurrency。

普通 CI 不调用真实付费 Provider。

---

# 7. P1：README 与当前代码严重不一致

## 7.1 当前已确认的旧描述

当前 README 仍包含类似：

```text
implements Stages 0–5 and Stage 6A
MockSearchProvider only; no real search API
SEARCH_PROVIDER=mock
Real Web Search remains intentionally out of scope
```

但代码已经有：

- `BaiduSearchProvider`
- Stage 6B L3 Semantic Knowledge Memory
- Eval Harness V2
- Pairwise Judge / Calibration
- Real Provider modes

必须修。

## 7.2 README 应成为当前唯一入口文档

至少重写以下部分：

### 项目定位

准确描述：

> Career Planning Buddy 是受控 workflow 型职业规划 Agent，
> 通过任务执行、复盘、三层记忆、真实联网知识和可复现 Eval
> 形成闭环。

不要写“fully production-grade”。

更准确：

```text
production-oriented portfolio / release-candidate system
```

并明确当前是单 Worker 设计。

### 架构图

更新为：

```text
Frontend
↓
FastAPI
↓
Agent Runtime
├─ L1 Working Memory
├─ L2 Personal Episodic Memory
├─ L3 Semantic Knowledge Memory
├─ Tool Registry
├─ DeepSeek/OpenAI-compatible Provider
├─ Baidu Search Provider
└─ PostgreSQL + pgvector

Eval Harness V2
Case → Experiment → Trial → Grade → Report
```

### 三层记忆

README 必须写清：

```text
L1：当前 Run 工作上下文
L2：用户私有长期执行记忆
L3：跨 Run 的来源可追溯求职知识
```

### Provider 模式

明确：

```text
Mock：默认测试/CI
Real LLM：OpenAI-compatible
Real Embedding：Local BGE
Real Search：Baidu
```

真实 Provider 失败不能 silent fallback 到 mock。

### Eval V2

补：

- Case；
- Experiment；
- Trial；
- Grade；
- fixture replay；
- Provider audit；
- token/error；
- pairwise；
- calibration；
- baseline。

同时说明：

> 未经足够真实样本和人工 calibration，
> Pairwise Judge 结果属于 diagnostic，不应被描述为最终质量真值。

### 启动

README 必须让新开发者不依赖聊天记录即可启动。

分别给：

1. Safe Mock Mode；
2. Local Real Provider Mode；
3. Docker Compose；
4. Test；
5. Eval V2。

---

# 8. P1：Agent Node 文档已经落后

当前：

```text
docs/model-design/agent-nodes/README.md
```

仍把 `distill_evidence` 描述为“尚未实现”。

但代码已经存在：

- `ExperienceAtomService.distill_run`
- Agent Executor best-effort distillation
- ExperienceAtomCandidate
- developer approval
- ExperienceAtom

要求：

- 更新 node index；
- 准确区分：

```text
memory_candidate_distiller
Review → Personal Memory Candidate

distill_evidence
SearchSource → ExperienceAtomCandidate
```

不要把 L2 与 L3 混淆。

检查其他 spec 是否也存在：

```text
文档说未实现，代码已实现
文档说已实现，代码不存在
```

统一修正。

---

# 9. P1：历史 Eval 文档与当前代码状态冲突

当前仓库包含很多逐 PR / handoff / implementation 记录。

例如：

```text
docs/implementation/eval-harness-v2.md
pr-9c-handoff.md
docs/implementation/eval-live-validation-2026-08-08.md
```

这些历史资料可以保留，但不能再充当“当前状态文档”。

处理方式：

- 不删除有价值的历史设计记录；
- 在文档顶部明确标注：

```text
Historical implementation note
Current status: see README / current-system-overview.md
```

- 对已完成内容更新状态；
- 对仍未完成内容保留；
- 不把过去某 PR 的 TODO 误认为当前缺口。

新增一个 canonical 当前架构文档：

```text
docs/architecture/current-system-overview.md
```

它应包含：

- 产品链路；
- Agent Runtime；
- 三层记忆；
- Provider；
- Search / RAG；
- Eval V2；
- DB；
- 前端；
- 安全边界；
- 当前已知限制。

---

# 10. P1：Docker Compose 无法正常选择真实 Baidu Search

## 10.1 当前问题

当前 `compose.yaml` 中：

```text
LLM_PROVIDER: ${COMPOSE_LLM_PROVIDER:-mock}
EMBEDDING_PROVIDER: ${COMPOSE_EMBEDDING_PROVIDER:-mock}
SEARCH_PROVIDER: mock
```

搜索被硬编码成 mock。

因此即使用户已经在本地配置 Baidu Search，
Docker 模式也无法通过环境变量显式切换。

## 10.2 修改要求

保留安全默认值：

```text
mock
```

增加显式 opt-in：

```text
COMPOSE_SEARCH_PROVIDER
COMPOSE_BAIDU_SEARCH_API_KEY
COMPOSE_BAIDU_SEARCH_BASE_URL
COMPOSE_BAIDU_SEARCH_EDITION
COMPOSE_BAIDU_SEARCH_MAX_RESULTS
COMPOSE_BAIDU_SEARCH_TIMEOUT_SECONDS
```

例如：

```yaml
SEARCH_PROVIDER: ${COMPOSE_SEARCH_PROVIDER:-mock}
BAIDU_SEARCH_API_KEY: ${COMPOSE_BAIDU_SEARCH_API_KEY:-}
```

不要把宿主机 `.env` 的 Secret 自动暴露给 frontend。

`.env.example` 更新模板，但真实值为空。

## 10.3 注意

本轮不要求把 Local BGE 完美容器化。

如果 Windows 宿主机 Local BGE 与 Docker volume 存在明显环境成本：

- Mock Compose 作为默认；
- README 明确真实 LLM/BGE/Baidu 推荐本地 backend 模式；
- 不为了展示强行加 GPU/CUDA。

---

# 11. P1：Eval Harness V2 缺少前端开发者入口

## 11.1 当前问题

后端已经有大量 Eval API：

```text
/api/v1/evals/...
pairwise calibration APIs
```

但当前前端 Router 只有：

```text
/dev/runs
```

没有 `/dev/evals`。

因此项目最有技术含量的新 Eval V2 对演示者不可视。

## 11.2 本轮实现一个最小 Dev Eval Console

新增：

```text
/dev/evals
```

只对：

```text
me.user.role === "dev"
```

显示入口。

后端仍必须由 `require_dev` 强制鉴权，前端隐藏不是安全边界。

### 页面只做必要能力

至少支持：

- Experiment 列表；
- 创建 mock/fixture Experiment；
- 查看状态；
- 查看进度；
- cancel；
- report summary；
- trial failure 分类；
- token / provider failure 摘要；
- baseline / variant；
- Pairwise / calibration 当前状态；
- 明确显示 `diagnostic_only` 或 `gate_eligible`。

不做大 Admin Dashboard。

### Live Eval

浏览器默认不要直接提供“一键跑付费 live 大实验”。

如果确实提供：

- 必须 dev-only；
- 明确二次确认；
- 默认小 case；
- UI 清楚标注会调用真实 Provider。

更推荐本轮前端只支持 mock/fixture；
live 保持 CLI/明确 API 操作。

---

# 12. P1/P2：开发者页面 Token 使用方式不一致

## 12.1 当前问题

普通 API client 使用：

```text
cpb_access_token
```

但：

```text
DeveloperRunsPage
frontend/src/api/dev.ts
```

又单独要求用户粘贴：

```text
career_buddy_dev_token
```

并自己拼 `Authorization`。

而 `MyPage` 已经通过：

```text
me.user.role === "dev"
```

控制 Developer 入口。

这是明显的 UX 和架构重复。

## 12.2 目标

如果现有 auth contract 支持：

- Dev API 统一使用普通登录得到的 `cpb_access_token`；
- `dev.ts` 复用公共 API client；
- DeveloperRunsPage 不再要求手工粘 JWT；
- `/dev/evals` 同样复用普通 auth。

后端 `require_dev` 继续校验 role。

### 禁止

不得新增：

```text
POST /make-me-dev
```

或任何 Guest 可以自助提升权限的 API。

如果演示需要 dev 账户：

- 优先使用已有开发机制；
- 如没有，可增加**仅本地 CLI**的安全 bootstrap；
- 不做公网 HTTP 权限提升接口。

---

# 13. P1：Eval V2 尚未进入 canonical CI/check 流程

## 13.1 当前问题

当前 CI / `scripts/check.ps1` 已运行：

- Ruff；
- Mypy；
- Alembic；
- Pytest；
- legacy `scripts.run_eval --no-persist`；
- frontend test/build。

但没有显式运行一次真正的 Eval Harness V2：

```text
Case → Experiment → Trial → Grade → Report
```

Pytest 很多并不等于整个 V2 CLI/runner 入口能从头跑通。

## 13.2 要求

增加一个**非常小、完全确定性、免费**的 Eval V2 smoke。

例如：

```text
1–3 cases
mock 或 fixture
不得使用 live Provider
```

可以是：

```text
python -m evals.v2 ...
```

具体 CLI 以当前实现为准，不猜参数。

加入：

- CI；
- `scripts/check.ps1`。

目标只验证：

```text
load dataset
→ create experiment
→ run trial
→ grade
→ report
```

不要在普通 CI：

- 调 DeepSeek；
- 调百度；
- 下载 BGE；
- 跑大 Pairwise sweep。

legacy Stage5/Stage6 runner 如仍用于兼容和回归，保留。

---

# 14. P1：OpenAPI / API / 前后端契约完整性

全面检查：

- Eval API；
- Pairwise calibration API；
- Memory API；
- Search / evidence 展示；
- Developer API；
- Plan source fields。

确认：

- OpenAPI snapshot；
- frontend types；
- backend schemas；
- status code；
- error code；
- pagination；
- auth；
- dev role；

一致。

如果已有 Snapshot 测试，则继续使用。

不要手工改 Snapshot 来掩盖未预期 API 变化。

---

# 15. P2：HomePage dead code

当前：

```text
frontend/src/pages/HomePage.tsx
frontend/src/pages/HomePage.test.tsx
```

存在，但 Router 不挂载 HomePage。

本轮不要为了“看起来完整”强行再设计一个 Landing Page。

先判断：

- 当前 Guest Login + root redirect 是否已形成完整首屏；
- HomePage 是否有任何业务引用。

如果确认 dead：

- 删除 HomePage；
- 删除对应无意义 test；
- 清理 import/style。

如果有真实产品价值才接路由，并说明理由。

---

# 16. P2：Baidu 来源域名分类需要低风险 hardening

当前来源分类如果仍使用简单：

```python
if "xxx.com" in hostname
```

字符串包含判断，可能导致：

```text
fake-gov.cn-example.com
```

之类被误判。

改成安全的 hostname 匹配 helper：

```python
def host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)
```

并：

- lowercase；
- IDNA / malformed host 安全处理；
- 测试子域；
- 测试恶意相似域名。

这只影响 `source_type / reliability` 先验，不把来源可靠性描述成事实真实性。

---

# 17. P2：代码中的 stale comment / docstring

例如当前 TrialRunner docstring 对 `live` 模式的描述与后面的 Audit 实现已经不完全一致。

系统性检查：

```text
代码已经改了
注释还描述旧实现
```

只修真实 stale 文档，不做大规模无意义改注释。

---

# 18. Eval 质量结论必须诚实

## 18.1 当前历史真实实验

历史 live validation 已经显示：

- B0 / B3 有真实 Provider timeout；
- 有 rate limiting；
- 一次小样本结果不足以证明 Full Agent 一定优于 Direct LLM。

最终 README / verification report 不得写：

```text
Our full agent is proven better than direct LLM
```

除非本轮真的获得足够稳定、完整、可比较的数据。

## 18.2 正确表达

当前可声明：

```text
Eval Harness V2 能系统比较 baseline 与 full-agent，
支持真实 Provider、Replay、Audit、Pairwise Judge 和 Calibration。
```

但质量优势需要：

- 足够完成的 paired trials；
- provider reliability；
- human calibration；
- 固定评测协议。

## 18.3 Human Calibration

如果真实人工标注数量不足：

- 保持 `diagnostic_only`；
- 不伪造 100 条 human label；
- 不让 Codex 自动生成“人工标签”来让 gate 通过；
- 在最终报告明确说明。

---

# 19. 产品主链最终人工审查

除了测试，还必须静态/实际检查下面链路是否有“代码存在但用户走不到”的情况：

```text
首次打开
→ Guest Login
→ Onboarding
→ Profile
→ Plan
→ Today
→ Task 完成/放弃
→ Review
→ Replan
→ MemoryCandidate
→ 用户确认 Memory
→ 新 Plan 使用 L2
→ 时效性请求
→ Web Search
→ SearchSource
→ ExperienceAtomCandidate
→ Developer approve
→ ExperienceAtom
→ RAG
→ Plan 引用 L3
→ Trace
→ Eval V2
```

逐步检查：

- loading；
- empty；
- error；
- degraded；
- cancelled；
- refresh；
- back/forward；
- SSE reconnect；
- API error 映射；
- 用户是否会卡死。

只修真实阻塞问题。

---

# 20. 数据库完整性审查

当前 Eval V2 新增了大量表和 migration。

检查所有当前模型：

- users；
- profiles；
- plans；
- tasks；
- reviews；
- memories；
- memory_candidates；
- search_sources；
- experience_atom_candidates；
- experience_atoms；
- agent_runs；
- steps/events/tool_calls；
- snapshots；
- eval_cases；
- experiments；
- trials；
- grades；
- provider_calls；
- pairwise / calibration tables。

重点检查：

- FK；
- unique；
- cascade；
- nullable；
- enum/status；
- index；
- idempotency；
- orphan row；
- duplicate row；
- user isolation；
- global knowledge 与 private memory 边界。

### Migration

不要根据 migration 文件名排序推断 lineage。

实际运行：

```text
alembic heads
alembic current
alembic upgrade head
```

确认只有一个有效 head。

如需新 migration，只做最小变更。

---

# 21. Security / Privacy 最终检查

必须确认：

### Secret

- `.env` ignored；
- SecretStr；
- API Key 不入 log；
- API Key 不入 Trace；
- API Key 不入 exception response；
- API Key 不入 frontend bundle。

### JWT

- SSE Authorization 不走 query string；
- Dev API 仍由 role 后端校验；
- 不开放权限提升 endpoint。

### Memory

- 未确认 L2 candidate 不进入模型；
- inactive memory 不进入模型；
- L2 不写入全局 L3；
- L3 candidate 必须有真实 source；
- Guest 不能 approve global knowledge。

### Search

- 用户 Query 进入 Trace 时只保存必要 hash/长度；
- Baidu 失败不回 Mock；
- 搜索结果不自动成为“真理”。

---

# 22. 本轮明确不做

除非审查发现是修复 P0 必须，否则禁止新增：

- 多 Agent；
- MCP；
- Redis；
- Celery；
- Kafka；
- 多 Worker 分布式调度；
- Kubernetes；
- 微服务拆分；
- 新向量数据库；
- 更换 BGE；
- GPU/CUDA 部署；
- HyDE；
- BM25 Hybrid；
- Reranker；
- Best-of-N；
- Self-Consistency；
- 大规模 Self-Reflection；
- 在线强制 LLM-as-Judge；
- 任意网页 crawler；
- 绕过 robots.txt；
- Admin 大后台；
- Analytics 大平台；
- 支付系统；
- 自动批准 ExperienceAtom；
- 自动生成假人工标注；
- 1000+ case 数据集；
- 因为 `graph.py` 大就全量拆文件；
- 因为 Eval 文件大就进行大规模重构。

理由：

> 当前目标是 v1.0 完整性、可信度、可演示和可维护性，而不是继续扩张技术栈。

---

# 23. 自动验收

修复完成后按当前项目真实命令执行。

至少：

## Root / Docker

```powershell
docker compose config
docker compose up -d postgres
docker compose ps
```

如果 Docker Desktop 未运行：

- 明确报告环境阻塞；
- 不得声称 Docker/DB 验收通过；
- 可继续执行不依赖 DB 的检查。

## Backend

```powershell
cd backend

.\.venv\Scripts\python -m alembic heads
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m alembic current

.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m mypy app tests scripts evals
.\.venv\Scripts\python -m pytest

.\.venv\Scripts\python -m scripts.run_eval --no-persist
```

再执行当前 Eval Harness V2 的 deterministic smoke。

具体命令必须根据：

```text
python -m evals.v2 --help
```

确认，不猜 CLI 参数。

## Frontend

```powershell
cd ..\frontend
npm test
npm run build
```

## Root

```powershell
cd ..
.\scripts\check.ps1
git diff --check
git status --short
```

同时验证：

- OpenAPI snapshot；
- `.env` 没有被 Git 跟踪；
- `docs/design-input` 无 diff；
- migration 单 head。

---

# 24. 最终真实 E2E

自动验收通过后，尝试一次最终小规模真实 E2E。

## 安全原则

- 不读取/输出 `.env`；
- 只通过 Settings 正常加载；
- 不打印 Secret；
- 不把 Key 放入命令行；
- 不加入普通 CI；
- 不扩大调用量。

## 优先使用已有配置

如环境中已经配置：

- DeepSeek/OpenAI-compatible LLM；
- Local BGE；
- Baidu Search；
- PostgreSQL / pgvector；

则验证：

```text
Guest
→ Profile
→ Plan
→ Task
→ Review
→ L2 MemoryCandidate
→ Confirm Memory
→ Replan 命中 L2
→ 一个明确时效性请求触发 Baidu Search
→ SearchSource
→ ExperienceAtomCandidate
→ developer approve
→ ExperienceAtom
→ RAG
→ 新 Plan 命中 L3
→ evidence_refs
→ Trace
```

再执行一个**小规模** Eval V2 live smoke。

目标是验证 harness 能跑真实 Provider，
不是在本轮重新跑昂贵的大型统计实验。

## 失败处理

如果真实 Provider 因：

- 网络；
- rate limit；
- timeout；
- quota；
- 本地模型路径；
- Docker；

失败：

必须准确归因。

不得：

- 自动换 Mock 后宣称真实成功；
- 无限增大 timeout；
- 删除失败数据；
- 修改业务规则只为让 E2E 通过。

---

# 25. 最终交付文件

必须新增：

```text
docs/review/final-gap-analysis-2026-08-09.md
docs/review/v1-release-verification-2026-08-09.md
docs/architecture/current-system-overview.md
```

并更新：

```text
README.md
```

必要时更新：

```text
.env.example
docs/model-design/agent-nodes/README.md
docs/implementation/eval-harness-v2.md
docs/implementation/eval-live-validation-2026-08-08.md
```

历史 handoff 文档如保留，应明确：

```text
Historical / superseded
```

不要删除 `docs/design-input`。

---

# 26. final-gap-analysis 必须明确哪些问题本轮不修

本轮完成后，P3/延后项必须列出“为什么现在不做”。

建议至少包含：

```text
单 Worker
没有 Redis
没有多副本
没有 Kubernetes
没有网页全文 crawler
没有 Hybrid RAG / Reranker
没有大规模真实 Eval
没有充分 Human Calibration
没有 Admin 大后台
```

正确表述：

> 当前是单 Worker、production-oriented 的求职作品 / Release Candidate，
> 并非宣称已经过真实大规模生产流量验证。

---

# 27. v1-release-verification 必须给最终结论

报告必须回答：

## 产品

- 新用户能否独立完成主流程？
- 有没有明显 dead end？

## Agent

- 真模型是否能运行？
- repair / fallback / cancel 是否仍工作？

## 三层记忆

- L1 是否压缩且可 Snapshot？
- L2 是否真正影响后续 Plan？
- L3 是否真正由真实来源产生并进入 RAG？

## Search/RAG

- 百度 Search 是否真实运行？
- 失败是否不会返回 Mock？
- evidence 是否可追溯？

## Eval

- Eval Harness V2 是否 deterministic smoke 可跑？
- live smoke 是否可跑？
- Provider failure 是否正确分类？
- Pairwise Judge 当前是 diagnostic 还是 gate-eligible？

## 数据

- Migration 是否单 head？
- 核心约束是否正确？

## 前端

- 产品页面是否完整？
- Dev Trace / Dev Eval 是否可访问？

## 文档

- README 是否与代码一致？

## 最终评级

只能从下面选择：

```text
A. v1.0 Release Candidate：可作为完整求职作品演示
B. 基本完整，但仍有明确阻塞问题
C. 尚未达到完整项目标准
```

如果不是 A，必须列出剩余阻塞项。

不要为了好看强行选 A。

---

# 28. 本轮最重要的验收标准

最终不是“代码更多”，而是下面几件事成立：

```text
1. README 与真实代码一致；
2. 三层记忆是真闭环，不是 README 概念；
3. Baidu Search 是真实 Provider，Mock 只用于测试；
4. Eval V2 能从 Case 跑到 Report；
5. Eval 的版本身份真实可复现；
6. Live Eval 遇到 429/timeout 有受控策略；
7. Provider transient failure 不再被误算成 Agent 质量失败；
8. Developer 能在前端看到 Trace 和最小 Eval Console；
9. Docker/本地启动方式清楚；
10. 全部自动测试、Migration、Build 真实通过；
11. 真实 E2E 成功或准确报告环境阻塞；
12. 项目不再依赖旧聊天才能理解和启动。
```

---

# 29. Codex 的工作方式

开始后：

### 第一步

先对照当前代码写：

```text
docs/review/final-gap-analysis-2026-08-09.md
```

并在终端用 10～20 行汇报真正发现的 P0/P1/P2。

### 第二步

直接开始修：

```text
P0
→ P1
→ 低风险 P2
```

不要等用户再次说“继续”。

### 第三步

运行自动验收。

### 第四步

运行可行的真实 E2E。

### 第五步

生成：

```text
docs/review/v1-release-verification-2026-08-09.md
docs/architecture/current-system-overview.md
```

### 第六步

最终终端汇报：

- 实际修复的问题；
- 修改文件；
- migration；
- 测试结果；
- Eval V2 smoke；
- 真实 E2E；
- 未解决项；
- 本轮明确未做；
- `git status`。

不要 commit，不要 push。
