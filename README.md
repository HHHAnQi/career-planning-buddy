# Career Planning Buddy

Career Planning Buddy is a controlled-workflow career-planning Agent. It closes the loop from profile and planning through daily execution, review, replanning, three-layer memory, source-traceable online knowledge, and reproducible evaluation. It is a production-oriented portfolio / release-candidate system, not a claim of large-scale production validation.

Runtime model access always goes through Provider protocols. Codex is an engineering tool and is not the application runtime model. The MVP intentionally uses one backend worker because its Agent and Eval executors are in-process.

## Architecture

```text
React frontend
      ↓
FastAPI HTTP/SSE API
      ↓
Controlled LangGraph runtime
├─ L1 Working Memory: current Run and compressed planning context
├─ L2 Personal Episodic Memory: confirmed user-private execution memory
├─ L3 Semantic Knowledge Memory: reviewed, source-traceable career knowledge
├─ Tool Registry: memory_lookup / rag_retrieve / web_search
├─ OpenAI-compatible LLM or deterministic Mock
├─ Baidu Search or deterministic Mock Search
└─ PostgreSQL 16 + pgvector

Eval Harness V2
Case → Experiment → Trial → Run → Grade → Report
```

The backend is Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 Async and Alembic. The frontend is React, strict TypeScript, Vite, React Router and TanStack Query. The MVP has no Redis, Celery, MCP, multi-agent framework, microservices or object storage.

## HTTP boundary guard

Every inbound request passes one guard middleware that records metrics and enforces per-identity rate limits (`docs/architecture/http-guard-and-metrics.md`):

- Rate limiting: fixed-window counter keyed by client IP plus Authorization hash (each authenticated user gets an independent budget). Exceeding the budget returns `429` with `Retry-After`. Health, metrics, docs and `OPTIONS` preflights are exempt. `RATE_LIMIT_PER_MINUTE=0` disables it (Compose default: 120).
- Metrics: `GET /metrics` exposes Prometheus text format — request totals with UUID/id-normalized path labels, latency count/sum, in-flight gauge and rate-limit rejections. In-process registry, no new dependency.
- Usage report: `GET /api/v1/dev/usage-report?days=30` (dev role) aggregates run counts by status, fallback rate, total/average cost in CNY, tokens, latency P50/P95/max, per-graph and per-day breakdowns, and provider-call health — all from existing `agent_runs` / `provider_calls` data with no extra instrumentation.

## Measured quality (deterministic mock evals)

Frozen datasets, deterministic graders, CI hard gates. Current numbers on the default Mock provider:

| Evaluation | Dataset | Result |
|---|---|---|
| 意图路由（规则路由 `intent-rule-v3`） | `intent-routing-v1`（23 例） | 23/23 = 100% |
| Stage 5 规划/修复/重规划/安全 | `stage5-v1`（30 例，11 个 Grader） | 30/30 = 100% |
| Stage 5（Eval V2 全硬门禁） | `stage5-v1`，每例 1 trial | 硬门禁通过率 1.0，首试成功率 1.0 |
| Stage 6 记忆/上下文选择 | `stage6-memory-context-v1`（12 例） | 12/12 = 100% |
| 文档检索（bge-m3 + bge-reranker） | `retrieval-v1`（10 例，语料级） | 纯向量 Recall@5 1.0；混合+真实重排 0.95 / MRR 1.00 / nDCG 0.96；混合 0.85；词法 0.85 |
| 真实运行（GLM-4.7，开发部署） | 58 条持久化 Run | 完成 89.7% / 降级 10.3%（业务修复路径）；延迟 P50 25.4s / P95 72.2s；token 输入 13.5 万 / 输出 7.6 万 |
| **stage5 真实基线（GLM-4.7，k=3）** | 30 例 × 3 trial = 90 次真实运行 | **硬门禁 72.2%**；首试成功率 73.3%（95%CI 55.6–85.8）；pass^3 70.0%；21 例 3/3 全过、8 例 0/3 全败（集中在工具调用与修复/重规划路径）；P50 26.1s / P95 47.0s |
| 回归测试 | backend tests | 790+ passing（schema/service/repository/API/runtime/eval） |

检索评测（`python -m scripts.run_retrieval_eval`）在冻结 golden set 上对比纯向量/词法/混合/混合+重排四种模式（bge-m3 向量 + GPU bge-reranker-v2-m3 重排）：纯向量 Recall@5 1.0；**混合+真实重排 0.95 / MRR 1.00 / nDCG@5 0.96**——重排把 RRF 融合的排序修正到首位命中率 100%（MRR 1.0），同时保留混合召回的鲁棒性。对照：确定性 Mock 重排只有 0.60（词法打分无法识别语义相关），验证了生产必须用真实 reranker（`RERANK_PROVIDER=tei`，兼容 HuggingFace TEI 协议的 GPU 服务）。报告记录 Provider，每个数字都可对照自己的配置复现。失败用例自动导出为结构化 bad case（`backend/evals/bad_cases/`），支持复现与归因。

Failed cases are exported automatically to `backend/evals/bad_cases/` as structured JSONL (runtime failures and hard-gate misses; user-cancelled trials are excluded) for reproduction and root-causing.

## Product flow and memory boundaries

The user flow is Guest Login → Profile → Plan → Today Tasks → Task feedback → Review → Replan → Memories → Plan history/evidence. Runs persist snapshots, steps, tool calls and SSE events before streaming; each Run has exactly one terminal event.

The three memory layers are deliberately different:

- L1 is the current Run working context: request, profile, plan, recent task/review history, deterministic compression, budgets and snapshots.
- L2 is user-private episodic memory: Review → MemoryCandidate → explicit confirm/reject → Memory → embedding/pgvector retrieval → later PlanningContext and evidence references. Unconfirmed or inactive items are excluded.
- L3 is shared semantic knowledge: Baidu Search → SearchSource → ExperienceAtomCandidate → developer review → ExperienceAtom → local BGE/pgvector → `rag_retrieve` and plan evidence. Search output is evidence, not automatically accepted truth, and L2 data never becomes global L3 data.

## Provider modes

Safe defaults use deterministic Mock LLM, embeddings and search. Real modes are explicit opt-ins:

- LLM: `openai_compatible`, including DeepSeek-compatible endpoints.
- Embedding: local BGE, with a pre-downloaded 1024-dimensional model directory.
- Search: `baidu`, using Baidu AI Search.
- Eval: `mock`, `fixture` or `live`; normal CI uses only free deterministic modes.

Missing or invalid real-provider configuration fails explicitly. Real-provider failures never silently fall back to Mock. Secrets must remain server-side and must never use browser-visible `VITE_` variables.

## Safe Mock mode with Docker

Requirements: Docker Desktop with Compose.

```powershell
Copy-Item .env.example .env
docker compose up --build -d
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/health
```

Open `http://localhost:5173`. Compose defaults to Mock providers, uses a named PostgreSQL volume, applies Alembic migrations, and starts exactly one Uvicorn worker.

Docker 与本机后端使用同一套 `LLM_*`、`SEARCH_*` 和 `EMBEDDING_*` 配置，不再维护 `COMPOSE_*` 副本。真实搜索通过 `SEARCH_PROVIDER=baidu` 显式启用。本地 Embedding 模型使用 `compose.embedding.yaml` 只读挂载；完整说明见 [`docs/third-party-integration/provider-configuration.md`](docs/third-party-integration/provider-configuration.md)。

## Local development and real providers

Requirements: Python 3.12, Node.js 20, npm and PostgreSQL 16 with pgvector. Start only the database if desired:

```powershell
docker compose up -d postgres
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.lock
.\.venv\Scripts\python -m pip install --no-deps -e .
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m uvicorn app.main:app --reload
```

In another terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Copy `.env.example` to the ignored root `.env` and select real providers there. Supply the LLM endpoint/model, a pre-downloaded local BGE path, and Baidu configuration only when using those modes. The application does not download model weights automatically. Never commit `.env` or credentials.

修改配置后运行 `cd backend && python -m scripts.audit_config`，检查 Settings、模板与 Compose 是否遗漏或漂移；运行 `python -m scripts.provider_status` 查看不包含密钥、端点和模型名的 Provider 配置状态。

## Developer surfaces

After normal login, users whose persisted server-side role is `dev` see:

- `/dev/runs`: redacted snapshots and hashes, steps, tools, persisted events, cost/latency and terminal invariants.
- `/dev/evals`: a small Experiment list/create/status/progress/cancel/report console for Mock/fixture runs, including runtime identity, failure categories, token summary and calibration state.

Both pages reuse the normal access token. Backend `require_dev` authorization remains the security boundary; there is no HTTP privilege-escalation endpoint. Legacy replay is explicitly named `legacy_trace_clone` and is not presented as Graph re-execution.

## Eval Harness V2

V2 freezes Dataset, Git/Graph/Prompt/Model/Tool/Context/Memory/Search/Harness versions and executes the real Case → Experiment → Trial → Run → Grade → Report path. It supports fixture record/replay, per-physical-call Provider audit, token/error accounting, deterministic graders, baseline/agent variants, Pairwise Judge and human calibration.

Discover and run a deterministic one-case smoke:

```powershell
cd backend
.\.venv\Scripts\python -m evals.v2 --help
.\.venv\Scripts\python -m evals.v2 run --dataset runtime-smoke --cases runtime-tool-error-01 --provider-mode mock --trial-count 1
```

The legacy Stage 5/Stage 6 regression suite remains available:

```powershell
.\.venv\Scripts\python -m scripts.run_eval --no-persist
```

`live` Eval is an explicit developer/CLI operation. It applies bounded transient retry, exponential backoff with jitter, `Retry-After`, pacing, concurrency and deadline/cancellation limits; 401/403, schema and business-contract failures are not retried. Without enough completed paired trials and genuine human labels, Pairwise output remains `diagnostic_only`, not final quality truth. Historical small live samples do not prove the full Agent is better than the direct-LLM baseline.

## Verification

Canonical local verification:

```powershell
.\scripts\check.ps1
```

The check runs Ruff, Mypy, Alembic upgrade, Pytest, legacy deterministic Eval, an Eval V2 end-to-end smoke, frontend tests and the production frontend build. GitHub Actions uses Python 3.12, Node.js 20, PostgreSQL/pgvector, locked dependencies, `APP_GIT_COMMIT=${{ github.sha }}` and Mock providers only.

Useful endpoints:

- API docs: `http://127.0.0.1:8000/docs`
- OpenAPI: `http://127.0.0.1:8000/openapi.json`
- Health: `GET /health`
- Prometheus metrics: `GET /metrics`
- Dev usage report (dev role): `GET /api/v1/dev/usage-report`

The current architecture and limits are maintained in `docs/architecture/current-system-overview.md`; release evidence is in `docs/review/v1-release-verification-2026-08-09.md`.
