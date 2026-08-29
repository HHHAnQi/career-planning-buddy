# PR-9c Handoff — Pairwise Judge + Calibration

> **Historical / superseded handoff.** This file records the state at a PR boundary and
> must not be used as the current gap list. Current behavior is documented in
> [`README.md`](README.md) and
> [`docs/architecture/current-system-overview.md`](docs/architecture/current-system-overview.md).

> 状态：交接文档。PR-9c.1 已实现但未 push（local `feat/eval`）；PR-9a / PR-9b 已合入 `origin/feat/eval`；PR-9c.2 尚未开工。
> 作者意图：下一会话以此文档作为入口，**不要靠聊天历史翻找上下文**。

## PR-9c.1 实现进度（本会话新增）

| 项目 | 值 |
|---|---|
| Commit 1 SHA | `ffd0d95` — Pairwise Judge domain core（51 单测） |
| Commit 2 SHA | local 未 push — alembic 0014 + 双表 ORM + repo（7 方法）+ service.run_pairwise_judge + config.judge_llm_* + 21 DB 测试 |
| Alembic head | `20260812_0014`（独立可 revert：up + down 均通过）|
| 测试 | `350/350 passed`（278 旧 + 51 commit1 + 21 commit2，含两个 pair_hash 语义保护测试）|
| 新文件 | `evals/v2/pairwise.py`、`evals/v2/judge.py`、`evals/v2/calibration.py`、`evals/v2/judge_factory.py`、`app/prompts/pairwise_judge.py`、`alembic/versions/20260812_0014_eval_pairwise_core.py`、5 测试文件 |

**Pair vs comparison_group 语义（push 前重点）**：
- `pair_hash` = stable Pair business identity。EXCLUDES `comparison_group_id` / `judge_run_id` / `judge_model` / prompt+rubric versions / display 顺序。INCLUDES：`schema_version="eval-trial-pair/v1"` + `case_id` + role-determined trial refs + baseline/candidate output hashes。任意 re-evaluation（新 prompt / model / comparison_group_id）若 outputs 未变 → 复用同一 Pair row。
- `comparison_group_id` = per-execution attribute，记录在 `EvalPairwiseJudgeResult.comparison_group_id` 列上（不在 pair 表上），tie together 一次 original+swapped 调用。
- `eval_trial_pairs` UNIQUE pair_hash + **非唯一** 复合索引 `(baseline_trial_id, candidate_trial_id)`：
  * same trials + same outputs  → pair_hash collide → 复用 Pair row
  * same trials + 变 outputs    → pair_hash 变化  → 新 Pair row 与旧 row 共存 attributable
- 保护测试：`test_pair_hash_excludes_comparison_group`、`test_same_pair_reuses_trial_pair_row_across_comparison_groups`。

**实际落地与原 plan 的偏差**：
- 原 plan 写的是单表 `eval_pairwise_scores`；最终落地为**双表**（`eval_trial_pairs` 与 `eval_pairwise_judge_results`），与 recon 报告一致（option C，user-mandated）。
- dimension 评分从 1-5 数字改为 **categorical verdicts**（a/b/tie/both_unacceptable），user-corrected。
- `invalid` 不是 winner 值；invalidity 落在 `judge_run_status='invalid_structured_output'`。
- `position_bias_rate = 1 - position_consistency_rate`（用户校正：balanced 50/50 ≠ bias=0）。
- 空输入 / 无 signal 返回 `MetricResult(value=None, sample_count=N)`，不返回 0.0。
- `FixturePairwiseJudge` 显式 mapping + fail-closed，无 pseudorandom fallback。
- **9c.1 不含 calibration 阈值 / κ / HTTP endpoint**（属 9c.2）。

**架构 invariant 守住**：
1. `evals/v2/*` 不 import `app/agent/*` / `app/harness/*`（依赖方向 Eval Harness → Agent Runtime；Judge 也是）。
2. Judge 只读 `JUDGE_ALLOWED_KINDS = {REQUEST_CONSTRAINTS, PLAN_PROJECTION, RUBRIC}` 的 AuthorizedView。
3. `PairwiseJudgeInput.display_a/display_b` 不含 baseline/candidate/model/auto-score 字段。
4. A/B 顺序由 `PositionVariant` 持久化（`raw_display_winner` + `normalized_winner` 都存）；`comparison_group_id` 在 Result 行上，不污染 Pair。
5. `input_hash` swap-invariant（先按 canonical_sha256 排序 outputs 再哈希）。
6. invalid output 一次 repair 后落 `invalid_structured_output`。
7. `JUDGE_PROMPT_VERSION`/`JUDGE_RUBRIC_VERSION` 锁 v1；版本变更会使旧 calibration 失效。
8. 无 calibration 工作流时，Judge 仅做诊断（9c.2 加 status 门禁）。
9. 无 model-generated human labels（无 `human_label` 字段；9c.2 引入）。
10. 0014 迁移独立可 revert（FK CASCADE：pair → results）。

---



---

## 0. 当前状态快照

| 项目 | 值 |
|---|---|
| 分支 | `feat/eval` |
| 最近 commit | `e953e6b PR-9b: failure taxonomy + live lifecycle + cancel/list/progress/regenerate` |
| 父 commit | `eca38d3 PR-9a: multi-trial statistics + report separation` |
| Alembic head | `20260810_0013`（PR-9b 加 `eval_experiments.cancel_requested_at / report_revision / report_content_hash`）|
| 测试 | `278/278 passed`（.env mask `AGENT_FEATURE_STAGE`+`AGENT_MAX_TOOL_ROUNDS` 之后；这两个字面量会让 `Settings()` 报 `Literal` 校验错 — known, 历史真因不在 PR-9b，是 `.env` 加载时 str→int coercion 未做）|
| `.env` | 已还原（`AGENT_FEATURE_STAGE=5`、`AGENT_MAX_TOOL_ROUNDS=0`）|
| 工作区 | clean；本地 == `origin/feat/eval` |

跑全量测试需要 mask `.env` 两个字段（PR-1 起）：
```bash
cp .env .env.backup
sed -i.bak 's/^AGENT_FEATURE_STAGE=/#AGENT_FEATURE_STAGE=/; s/^AGENT_MAX_TOOL_ROUNDS=/#AGENT_MAX_TOOL_ROUNDS=/' .env
cd backend && /opt/anaconda3/envs/cp/bin/python -m pytest tests/ -q
cd .. && mv .env.backup .env && rm -f .env.bak
```

---

## 1. PR-9a 已完成（已 push，SHA `eca38d3`）

### 新增
- `backend/evals/v2/stats.py` — Wilson/Normal CI、`CaseStat`、`ExperimentStat`、`compute_case_stats`、`compute_experiment_stats`。PR-9b 已升级为基于 `app/harness/errors` taxonomy 派生 `RUNTIME_FAILURE_CODES`/`USER_CANCEL_CODES`。
- `backend/tests/evals_v2/test_eval_stats.py`（14 单测）
- `backend/tests/evals_v2/test_eval_stats_integration.py`（2 DB 集成）

### 修改
- `TrialSummary.trial_index: int = 0`（向后兼容 default）。
- `ExperimentReport.case_stats: dict[str, CaseStat]` + `experiment_stats: ExperimentStat | None`。
- `EvalRunReportResponse.case_stats` + `experiment_stats`（默认空，向后兼容）。
- `scripts/generate_openapi` 重生成快照。

PR-9b 已对 `CaseStat` / `ExperimentStat` 增加 `configuration_failure_count` + `cancelled_by_user_count`（向后不兼容的手工构造路径需要补这两字段）。

---

## 2. PR-9b 已完成（已 push，SHA `e953e6b`）

### Cluster A — Failure Taxonomy
- `backend/app/harness/errors.py`（新）— `EvalFailureCode`/`FailureCategory` StrEnum + `normalize_failure_code()` + `RUNTIME_FAILURE_CATEGORIES` + `RUNTIME_FAILURE_CODES`/`USER_CANCEL_CODES`/`CONFIGURATION_FAILURE_CODES` 集合。
- `app/harness/provider_calls/recorder.py` — fix `exc.code` bug（取代 `type(exc).__name__`）。
- `evals/v2/stats.py` — bucket 改派生自 taxonomy；新 bucket 字段在 `CaseStat` / `ExperimentStat`。

⚠️ **架构 invariant**：`app/agent/errors.py` 保持 agent layer 独立，**禁止 import `app/harness/errors`**（依赖方向 Eval Harness -> Agent）。新会话必须保持这条边界。

### Cluster B — alembic 0013 + List/Progress/Regenerate
- `backend/alembic/versions/20260810_0013_eval_live_lifecycle.py` — 3 列 + partial index。
- `EvalExperiment` ORM 加 `cancel_requested_at` / `report_revision` / `report_content_hash`。
- `EvalRepository.list_experiments(status, limit, offset)`。
- 3 新 endpoint：
  - `GET /api/v1/eval/runs`（分页 list）
  - `GET /api/v1/eval/runs/{id}/progress`（轻量进度，不推断 current_step）
  - `POST /api/v1/eval/runs/{id}/report/regenerate`（**纯 SELECT**，content-hash 驱动 revision）
- `EvalService.regenerate_report()` — borrow `report_content_hash` 比对；变化才 bump。

### Cluster C — Cancel
- `POST /api/v1/eval/runs/{id}/cancel`，返回 202 Accepted + `cancel_requested: bool`。
- `cancel_requested_at` 是 FACT 记录，**不是终态**（`status=='cancelled'` 才是）。
- `EvalRunnerExecutor.recover_interrupted()` crash-evidence 优先：crash 后 experiments = failed + 所有非 terminal trial = `PROCESS_INTERRUPTED`，**即使 cancel_requested_at 已记也不标 cancelled**。

### Cluster D — Live Provider
- Settings: `eval_audit_live_calls: bool = True` / `eval_provider_seed_mode: Literal[...]`（"provider_seed" 默认）。
- `TrialRunner._build_executor` live 分支：audit_live_calls=True 时安装 `ProviderCallRecorder` + `Audit*Provider` wrapper（call shape 与 mock/fixture 同形）。
- `EvalService.create_experiment` 守卫：`execution_mode=live_provider && settings.llm_provider=='mock'` → 409 `EVAL_PROVIDER_MODE_INVALID`。

### 已知 PR-9b 未做（PR-9b.next 或与 PR-9c 协同）
- **RetryingProvider wrapper**（Q3 决策：在 Audit 外层）
- **ProviderCapabilities.supports_seed + SeedContext**（Q7 决策：seed 不强映射 temperature；pass-through to inner provider）
- **`provider_calls.retry_attempt >= 1` 的 ck 迁移**（现在仍 default 0）

---

## 3. PR-9c 待开工（拆两子 PR，已与用户对齐）

### PR-9c.1 — Pairwise Judge Core

**明确范围**：
- Pair contract（baseline + candidate trial_id、case_id、ab_seed、judge_run_id、两个 outcome projection）
- 授权输入投影：Judge 只读 `EvidenceKind.REQUEST_CONSTRAINTS` + `EvidenceKind.PLAN_PROJECTION`，**禁读** transcript / raw LLM response（防泄漏 + 信息最小化）
- A/B 盲化与顺序交换（ab_seed 哈希决定 swap；raw winner 写 DB 时 unswap 到 `(baseline_trial_id, candidate_trial_id)` 真值）
- Judge Provider Protocol：轻量 `EvalPairwiseJudge` 类（直接 httpx + OpenAI-compatible；**不依赖 langchain**）
- Judge result / version contract：`EvalPairwiseScore` ORM + alembic 0014 + 5 维度分数（actionability / alignment / personalization / clarity / consistency，整数 1-5）+ winner ∈ {a, b, tie}
- Fixture Judge（mock）：单测不需要 live LLM 凭据
- agreement 和 position-bias 纯函数（`evals/v2/calibration.py`）
- 最小持久化 + 单元测试
- **不实现** 人工评审 HTTP 和正式校准流程

**迁移**：`alembic/versions/20260812_0014_eval_pairwise_scores.py`（用户习惯是日期 + 序号；下一会话开工时取当天日期，序号 0014）
- 表名建议：`eval_pairwise_scores`
- 字段（抄原 plan）：id（UUID PK）、`baseline_experiment_id` + FK、`candidate_experiment_id` + FK、`baseline_trial_id` + FK、`candidate_trial_id` + FK、`case_id`、`judge_run_id`、`winner` (ck ∈ a/b/tie)、5 × `*_score` (Numeric 4,2 ck between 1 and 5)、`ab_position_swapped` (bool)、`calibrated` (bool default false)、usage token fields、`judge_model`、`judge_latency_ms`、`evidence_json`、`rationale`、`created_at`
- UQ `(baseline_trial_id, candidate_trial_id, judge_run_id)` → 客户端重试幂等
- Index `(baseline_experiment_id)`、`(candidate_experiment_id)`、`(case_id)`

**新文件**：
- `evals/v2/judge.py`（`EvalPairwiseJudge`、`PairwiseJudgePrompt`、`PairwiseJudgeOutput`）
- `evals/v2/calibration.py`（agreement_rate、position_bias_rate 纯函数；κ 留 9c.2）
- `evals/v2/pairwise_loader.py`（Pair contract loader；sources from real Trials，**不手工凭空编**）
- `tests/evals_v2/test_pairwise_judge.py`

**编辑**：
- `app/models/eval.py`（+ `EvalPairwiseScore` ORM）
- `app/repositories/evals.py`（`create_pairwise_score` / `list_pairwise_scores`）
- 没有 HTTP endpoint（9c.1 只交付 core + tests）

**退出门禁**：
- alembic 0014 上/下都跑通
- EvalPairwiseJudge.build_prompt 渲染 rubric + true AB swap 被 swap 回来的 unswap 一致（unit test）
- PairwiseJudgeOutput schema 校验 invalid winner 拒绝、dimension 超出 1-5 拒绝（3 tests）
- `agreement_rate` 和 `position_bias_rate` 纯函数算式（4 tests）
- 278 → ~288 全绿

### PR-9c.2 — Calibration Workflow

**明确范围**：
- Calibration JSONL loader + 文件 hash（hash 写进 calibration report，让审计可追溯）
- **人工 label 导入工具**（import 时 `human_label` 字段**单独写表 / 独立字段**，不允许任何代码自动生成冒充）
- agreement / confusion matrix / per-dimension Cohens kappa / per-slice
- position-bias 报告（swap-a-b 的 winner imbalance）
- `calibration_status` enum：`passing | failing | insufficient`（阈值：agreement ≥ 0.70 AND position_bias_rate ≤ 0.15 → passing；< 0.60 agreement → failing；< 10 pair → insufficient）
- HTTP endpoints：`POST /api/v1/eval/runs/{baseline_exp}/pairwise/run` + `GET /api/v1/eval/runs/{exp}/pairwise/result` + `GET /api/v1/eval/pairwise/calibration`
- 集成测试
- **正式人工校准**（≥100 confirmed human-judged pair）

**Calibration 数据分工（用户决策，强制约束）**：
1. Codex 从真实 Baseline/Candidate Trial 生成 **20-30 对 smoke 草稿**；`human_label` 字段保持空或 `null`
2. Codex **可单独生成** `suggested_label`（helps the human reviewer），但**绝对不得写入 `human_label`**
3. 人工负责最终确认和裁决（reviewer 申请 commit 时填 `human_label`）
4. 20-30 对仅用于 **dev smoke**；正式退出门禁仍为 **≥100 个有效人工确认/裁决 Pair**
5. Pair **优先来自真实 Trial**（pull from stages 5 / runtime_smoke 已有 outcome），不得主要依靠手工凭空编造

**新文件**：
- `evals/v2/datasets/pairwise-calibration-v1.jsonl`（20-30 行；**source from real Trials**；`human_label: null`）
- `evals/v2/datasets/pairwise-calibration-labels.jsonl`（reviewer 确认后填，**单独文件 / 单独 commit path**）
- `evals/v2/calibration.py`（扩展：Cohens κ + per-slice + status）
- `tests/evals_v2/test_calibrated_dataset.py`
- `tests/evals_v2/test_pairwise_api.py`

**编辑**：
- `app/api/evals.py`（3 新 endpoint）
- `app/schemas/evals.py`（4 新 schema）
- `app/services/evals.py`（`run_pairwise_judge` + `list_pairwise_scores` + `compute_pairwise_metrics`）
- `tests/conftest.py`（StubEvalRunnerExecutor 扩 + `StubPairwiseJudge` 或单独 stub）
- `tests/snapshots/openapi.json`（auto-regen）

---

## 4. 当前 report / evidence 数据契约（PR-9c 必读）

### AuthorizedView 投影（PR-9c Judge 输入）
- `EvidenceKind.REQUEST_CONSTRAINTS`：含 `expect_constraint` string，case.provider_fixtures 注入
- `EvidenceKind.PLAN_PROJECTION`：完整 PlanCandidate（含 `evidence_refs`、`tasks`、`summary`、`weekly_focus`）
- `EvidenceKind.EVIDENCE_VISIBLE_REFS`：visible refs 集合
- `EvidenceKind.EXPECTED_CITATIONS_MAP`（PR-8b）：fixture_memory_id → Memory UUID map

存储位置：`eval_evidence_items` 表（EvalEvidenceItem），`EvalRepository.list_evidence_items(trial_id)` 拉取；`pr` 数量大致 ~20 行 per trial（含 PLAN_PROJECTION、EVENT_PROJECTION × n、TOOL_CALL_PROJECTION × n、REQUEST_CONSTRAINTS、RUN_METRICS、OUTCOME_STATUS、调 grade 后还加 6 行 EvalScore 行 per grader）。

### ExperimentReport 形状
```
{
  "experiment_id": UUID str,
  "experiment_status": literal["draft","running","completed","failed","cancelled"],
  "trial_count": int,
  "completed_trial_count": int,
  "scored_trial_count": int,
  "hard_gate_pass_fraction": float,
  "any_score_generated": bool,
  "trials": [TrialSummary_dict],
  "counterfactual_pairs": [CounterfactualPairDiff_dict],   # 空 when 无 variant 桩
  "case_stats": {case_id: CaseStat_dict},                  # PR-9a
  "experiment_stats": ExperimentStat_dict | None,          # PR-9a
  # HTTP report layer 额外加：
  "revision": int,                                          # PR-9b
  "cancel_requested_at": datetime | None,                  # PR-9b
}
```

`TrialSummary`：`trial_id, case_id, status, run_status, result_kind, tokens_in, tokens_out, latency_ms, error_code, terminal_event_count, tool_call_count, variant=None, counterfactual_group_id=None, trial_index=0`

**Pairwise Judge 不修改 ExperimentReport 形状**；它在 Pairwise 结果 endpoint 返回单独 schema。但是 `EvalPairwiseScore` 行将持久化在 `eval_pairwise_scores` 表，**绝不混入 eval_scores 表**（避免污染 hard_gate_pass_fraction）。

---

## 5. 待决问题（用户已部分定，但新会话需重新对齐）

| 问题 | 已知偏好 | 需重新对齐的细节 |
|---|---|---|
| Judge LLM 凭据 | 复用 `settings.llm_*` | 是否 Product 环境用 dev-privileged model（避免同一被评模型当 Judge 的 self-评审 bias） |
| Pair output 投影的字段 min-set | summary + evidence_refs + tasks | 是否加 `weekly_focus` / plan JSON 的 keywords |
| Judge prompt 语言 | 简体中文 | system prompt 是否要包含 ab bias detection hint |
| Calibration `calibration_status` 阈值 | spec 默认 agreement ≥ 0.70 / position_bias ≤ 0.15 | 是否 9c.2 落地为 Settings 可调（不硬编码）|
| Pairwise 是否纳入 experiment report response | 9c.1 决策：不纳入 | 复用 `/report/regenerate` endpoint 不动；只通过 `/pairwise/result` 独立返回 |
| Live LLM 调用 cost 来源 | real provider budget（同 PR-9b live） | 是否限制 Judge 在 Candidate Eval phase（避免 prod-运行时调用） |

---

## 6. 命令样例（下一会话复用）

```bash
# Branch + base
cd "/Users/huanqi/Accompany Project/career-planning-buddy-ly"
git checkout feat/eval
git pull

# Pre-test mask .env（每次跑 pytest 前）
cp .env .env.backup
sed -i.bak 's/^AGENT_FEATURE_STAGE=/#AGENT_FEATURE_STAGE=/; s/^AGENT_MAX_TOOL_ROUNDS=/#AGENT_MAX_TOOL_ROUNDS=/' .env

# Run
cd backend && /opt/anaconda3/envs/cp/bin/python -m pytest tests/ -q

# Restore
cd .. && mv .env.backup .env && rm -f .env.bak
```

---

## 7. 下一会话开工 checklist

1. **本文档第 0 节**：复跑 `git status` + `git log --oneline -3` 确认仍 clean + head 仍 `e953e6b`
2. **复跑全量 pytest**：mask `.env` 后跑一次，期望 278/278
3. **确认 alembic head**：`alembic current` 期望显示 `20260810_0013 (head)`
4. **读 PR-9c plan**（旧会话已批准但 9c 未开工；本 handoff §3 含拆分后的范围；plan 不够细需要重进 EnterPlanMode + 用户批准）
5. **不要立即写 code**：先进 EnterPlanMode 写 PR-9c.1 final plan（基于本 handoff §3.1 + recon，避免依赖本会话的 chat history）
6. **Calibration 草稿生成**：PR-9c.1 完成后，下一会话先用真实 Trial 跑 baseline vs candidate，生成 20-30 行 jsonl（`human_label: null`）；**绝不手工 author**；让 reviewer 填 label 是 PR-9c.2 任务
7. **保持架构 invariant**：Agent layer (`app/agent/*`) 不得 import Eval Harness layer (`app/harness/*` / `evals/v2/*`)

---

## 8. PR-9c.2 final state (frozen)

> 状态：PR-9c.2 engineering gate = **PASS** / PR-9c.2 = **CLOSED** / PR-9 formal human-calibration gate = **BLOCKED** / Overall PR-9 = **NOT CLOSED**。本节冻结 PR-9c.2 最终基线,作为下一会话入口。

### 8.1 Final baseline

| 项目 | 值 |
|---|---|
| 分支 | `feat/eval` |
| PR-9c.2 final SHA | `0122bbc2e898ee75d03182220775a69bfeeb20e8` |
| HEAD == origin/feat/eval | ✓ |
| Working tree | clean |
| Alembic head | `20260815_0015` (`eval_pairwise_calibration` 4 表;0016 未引入) |
| Endpoints | **9**（非 10;上一轮 handoff 报告误计。详 §8.5）|
| Tests | `492 passed`（PR-9c.1 完成时 350;PR-9c.2 共 +142）|
| ruff (app/evals/tests) | 0 errors |
| mypy (app/evals/tests) | 0 errors / 205 files |
| `test_openapi_snapshot.py` | passes（snapshot 已 regen 到 0122bbc）|
| 工程门禁 | PASS |
| 正式人工校准门禁 | BLOCKED |

### 8.2 PR-9c.2 commit chain

```
656336c  Commit 1    Calibration domain core (review_surface / sampler / loader / metrics + 63 tests)
3ef92bd  Commit 2    Calibration persistence (alembic 0015 双表 / 4 ORM / Repository 5 领域 / Service 6 能力 / 43 DB tests)
4ee4efa  Commit 3    Pairwise Calibration HTTP + SweepExecutor (9 endpoints)
c7d3431  Commit 3.1  Executor semantics + Review workflow fixes (reviewer issues #1,#2,#3,#4,#6,#7)
0122bbc  Commit 3.2  Close 3 remaining reviewer items (#1 review_token required, #2 dedicated advisory-lock connection, #3 wire compute_calibration_status)
```

### 8.3 PR-9c.2 endpoint catalog（9 个）

```
POST /api/v1/eval/runs/{baseline_exp}/pairwise/run                              spawn sweep
GET  /api/v1/eval/runs/{baseline_exp}/pairwise/run/{sweep_id}                   status
POST /api/v1/eval/runs/{baseline_exp}/pairwise/run/{sweep_id}/cancel            staging only
GET  /api/v1/eval/runs/pairwise/pairs/{pair_id}/review-surface?sweep_id=…       blinded review surface + review_token
POST /api/v1/eval/runs/pairwise/annotations                                     primary OR adjudication（review_token 必填）
GET  /api/v1/eval/runs/pairwise/annotations/{pair_id}                           按 pair 列出
POST /api/v1/eval/pairwise/calibration                                          create report（含真实 calibration_status）
GET  /api/v1/eval/pairwise/calibration/{dataset}/{version}/latest
GET  /api/v1/eval/pairwise/calibration/{dataset}/{version}/history
```

`GET /runs/pairwise/annotations/{pair_id}` 的 `?sweep_id=…` 是 query param,不是独立 endpoint。

### 8.4 Calibration status now wired(Commit 3.2 issue #3 解除)

`compute_calibration_status`（Commit 1 已数据驱动的纯函数）自 Commit 3.2 起真正接入 production report pipeline:

```
POST /pairwise/calibration
  └── _compute_calibration_status_from_snapshots():
        valid_human_pair_count      ← annotation snapshot（≥2 distinct primary reviewers per pair）
        position_pair_count         ← Σ sweep.position_pair_count（结构 counter）
        position_metric_sample_count ← pairs where BOTH required results are
                                         judged_run_status='completed'
                                         AND normalized_winner IS NOT NULL
        agreement                   ← exact-match Judge-vs-human over intersection
        position_bias               ← fraction of decisive pairs whose verdict
                                       differs across the two position variants
  └── compute_calibration_status(position_pair_count=position_metric_sample_count)
  └── report_payload.calibration_status / usage_mode / agreement / position_bias /
                                  valid_human_pair_count /
                                  position_pair_count /
                                  position_metric_sample_count /
                                  agreement_sample_count
```

**关键**：正式门槛（`compute_calibration_status`）使用 `position_metric_sample_count`,**not** `position_pair_count`。`judge_run_status='invalid_structured_output'` 的 Result 行存在但仍不计入 metric 分母。

`report.report_payload` 现在持久化全部真实字段,GET `/pairwise/calibration/{...}/latest|history` 通过 `dict(report.report_payload)` 读取真实值。Commit 3.1 之前的 hard-default `payload.get("calibration_status", "insufficient")` 已废止。

### 8.5 文档更正记录

1. **Endpoint 数 (3.1 报告误写 10)**：实为 9（§8.3）。已在 module docstring 修正。
2. **Commit 3.2 新增 test 数 (3.2 报告误写 11)**：实为 **8**:
   - Token 3：`test_annotation_without_review_token_is_rejected` / `test_annotation_with_token_for_other_pair_is_rejected` / `test_annotation_token_for_other_reviewer_is_rejected`
   - Lock 4：`test_advisory_lock_unlock_use_same_connection` / `test_second_executor_cannot_drive_locked_sweep` / `test_lock_released_after_executor_exception` / `test_lock_released_after_cancelled_sweep`
   - Calibration 1：`test_calibration_report_status_is_data_driven`
   - Pytest 由 Commit 3.1 时的 484 增至 492,净增 8,与列表一致。

### 8.6 Executor state machine（Commit 3.2 修订）

```
submit → _execute → _drive_sweep:
    async with engine.connect() as lock_conn:         # 物理连接独占,Commit 3.2 issue #2
        acquired = pg_try_advisory_lock(k1, k2)        # 非阻塞,失败即 return
        if not acquired: return                        # 另一 worker 持锁,本 worker 让位
        try:
            await _recover_running_items(sweep_id)
            loop:
                done = await _pump_one_item(...)        # 每 item 独立短 txn
                if done: return
        finally:
            unlocked = pg_advisory_unlock(k1, k2)
            assert unlocked is True                    # 永不 silent 丢失锁;否则 RuntimeError
```

`_advisory_key_parts` 高位重解释为 signed int4,允许任意 sweep UUID 落在 Postgres advisory int4 区间。

Recovery（`_recover_running_items`）入口开自己的短 session,实现委派 `_recover_running_items_in_session`,供测试以外层 `db_session` 驱动（避免写落到 rollback-root 之外）。逻辑:

- `running + 无 JudgeResult` → CAS requeue 到 `queued`
- `running + 已有 JudgeResult` → CAS 直 `completed`（无 Provider 调用） + `_apply_pair_deltas`

Pair 计数（Commit 3.1 修订,Commit 3.2 未改）：

- `completed_pair` 仅在 **第二** 个 required Item terminal 时 +1
- `position_pair` 仅在两个 sibling 均 `completed` 且均有 `judge_result_id` 时 +1
- `failed` / `cancelled` sibling 不计 `position_pair`

`run_pairwise_judge` 已加 idempotency guard:replay 同一 `judge_run_id` 时先查 `get_pair_by_hash` + `get_judge_result`,命中即返回已存 (pair, result),避开 UNIQUE(`judge_run_id`) IntegrityError。

### 8.7 正式人工校准门禁（仍 BLOCKED）

```
PR-9 formal human-calibration gate = BLOCKED
calibration_status                  = insufficient
usage_mode                          = diagnostic_only
valid_human_pair_count              = 0 / 100
position_metric_sample_count        = 0 / 100
```

**非硬编码**:`compute_calibration_status` 真实数据驱动,只待真实数据。`insufficient` 是 `valid_human_pair_count=0` 的正确推导。

### 8.8 数据集边界（用户冻结,不可混用）

| 数据集 | 用途 | 规模 | 预期 status | 预期 usage_mode |
|---|---|---|---|---|
| `pairwise-calibration-v0-dev-smoke` | smoke 工程端到端验证 | **20–30** 真 graded Trial pair | 永远 `insufficient` | 永远 `diagnostic_only` |
| `pairwise-calibration-v1` | 正式 Calibration Gate | ≥100 valid 人工 pair | 根据 agreement / position_bias 推导 | passing 时 `gate_eligible` |

**Smoke 不扩成 100,不借用为正式 v1**。一个有效人工 pair = 双 primary consensus 或 双 primary disagreement + 第三人 adjudication;100 pair 至少 ~200 次 primary annotation 加分歧的 adjudication。

### 8.9 下一会话任务（不进 PR-10,不混用数据集）

```
阶段 A  导出真实 v0-dev-smoke Dataset（20–30 真 graded Trial pair,human label null）
        端到端验证：冻结 → sweep → original/swapped Judge → GET review-surface →
                  POST annotations（必带 review_token）→ 双人共识 / adjudication →
                  POST /pairwise/calibration
        预期：calibration_status=insufficient, usage_mode=diagnostic_only

阶段 B  pairwise-calibration-v1:≥100 valid 人工 pair
        valid_human_pair_count ≥ 100 且 position_metric_sample_count ≥ 100

阶段 C  冻结 v1 校准报告
        agreement ≥ 0.70 且 position_bias ≤ 0.15 → passing / gate_eligible → close PR-9
        否则 failing / diagnostic_only → 不调阈值,定位 prompt/rubric/model/position 问题重版本化

阶段 D  PR-10:CI / Developer Trace / Production Backtest / Bad Case 回流 / Online Eval
```

### 8.10 命令样例（下一会话复用）

```bash
cd "/Users/huanqi/Accompany Project/career-planning-buddy-ly"
git checkout feat/eval
git pull
git rev-parse HEAD   # 期望 0122bbc...
git status --short   # 期望空

# Pre-test mask .env（沿 PR-1 起,Settings Literal coercion 限制）
cp .env .env.backup
sed -i.bak 's/^AGENT_FEATURE_STAGE=/#AGENT_FEATURE_STAGE=/; s/^AGENT_MAX_TOOL_ROUNDS=/#AGENT_MAX_TOOL_ROUNDS=/' .env

cd backend && /opt/anaconda3/envs/cp/bin/python -m pytest tests/ -q
/opt/anaconda3/envs/cp/bin/python -m ruff check app/ evals/ tests/
/opt/anaconda3/envs/cp/bin/python -m mypy app/ evals/ tests/
PYTHONPATH=. /opt/anaconda3/envs/cp/bin/python scripts/generate_openapi.py
/opt/anaconda3/envs/cp/bin/python -m pytest tests/test_openapi_snapshot.py -q

cd .. && mv .env.backup .env && rm -f .env.bak
```

---

## 9. PR-9c.2 Commit 3.3 — Stage A plumbing(2026-08-05)

> 状态:Stage A 的 **plumbing** 已交付(commit `cced011`),但**真实 graded Trial pair dataset 的实际导出**尚未执行。Commit 3.3 落地了使 dataset 端到端可被消费的 3 个先决条件;真实数据导出 + HTTP 端到端验证(handoff §8.9 阶段 A 的 runbook)是下一会话的入口。

### 9.1 Commit 3.3 SHA & 改动概要

```
cced011 PR-9c.2 Commit 3.3 — Stage A: real pairwise-calibration-v0-dev-smoke plumbing
```

| 文件 | 改动 |
|---|---|
| `alembic/versions/20260815_0016_eval_pairwise_sweep_fixture_mapping.py` | 新增 `eval_pairwise_sweeps.fixture_mapping JSONB NULL` 列 |
| `app/api/pairwise_calibration.py` | `_materialize_sweep` 真正调用 `materialize_sweep_items`,把 `bundle.lines` 转成 `EvalTrialPair` + 2 个 `SweepItemSeed` per line |
| `app/harness/pairwise_sweep_executor.py` | `_drive_sweep` 进入处 load sweep 一次,把 `sweep.fixture_mapping` 反序列化为 `PairwiseJudgeOutput` 后传给 `build_pairwise_judge` |
| `app/models/eval.py` | + `fixture_mapping` JSONB 列 |
| `app/schemas/evals.py` | `PairwiseRunRequest` 加可选 `fixture_mapping` |
| `scripts/export_pairwise_dataset.py` | 新增 — 从 DB 的 `EvalTrialPair` + `EvalEvidenceItem` 导出 loader 兼容的 JSONL + manifest,导出后立刻 reciprocal load 验证 hash 一致 |
| `tests/evals_v2/test_pairwise_calibration_smoke_e2e.py` | 新增 4 测试(service-layer 验证) |

Commit 3.3 新增测试:**4 条**(非 11;handoff §8.5 至前报告的 8/11 校正延续,本 commit 净增 4)。pytest 总数 `496 passed`。

### 9.2 Recon 揭示的 3 个 Stage A 阻塞缺陷(已修复)

| Gap | 修复前行为 | 修复后 |
|---|---|---|
| Gap 1 | 无 exporter(`pairwise_sampler.py` 是纯 fn,无 IO) | `scripts/export_pairwise_dataset.py` 读 `EvalTrialPair` + `EvalEvidenceItem` → 生成 loader 兼容的 JSONL/manifest |
| Gap 2 | `_materialize_sweep` 只 build Sweep row 不 build SweepItem(Commit 3.1 报告显式遗留) | 现在每 line → idempotent `get_or_create_pair` + 2 个 `SweepItemSeed`(baseline + swapped) |
| Gap 3 | Executor `build_pairwise_judge(settings)` 空 mapping → 全部 pair fail-closed `invalid_structured_output` | 新增 `sweep.fixture_mapping` JSONB(executor 读一次);fixture-mode smoke 现可注入 mapping → 真 winner |

### 9.3 数据库迁移

```
alembic current:   20260815_0016 (PR-9c.2 Commit 3.3)
HEAD lineage:      20260812_0014 (PR-9c.1)
                   20260815_0015 (PR-9c.2 Commit 2)
                   20260815_0016 (PR-9c.2 Commit 3.3)  ← 新增
```

### 9.4 下一会话入口 — Stage A 端到端 runbook(手动执行)

```bash
cd "/Users/huanqi/Accompany Project/career-planning-buddy-ly"

# 1. 准备两个真实 graded baseline / candidate experiments。
#    使用 fixture mode 不调外部 LLM:
#    在已有 EvalService.create_experiment 路径下各跑 1 个
#    (eval_provider_mode=fixture / judge_llm_provider=fixture)。
#    记录 baseline_experiment_id 和 candidate_experiment_id。

# 2. 导出真实 v0-dev-smoke dataset
cd backend
PYTHONPATH=. /opt/anaconda3/envs/cp/bin/python scripts/export_pairwise_dataset.py \
    --baseline-experiment-id <BASELINE_UUID> \
    --candidate-experiment-id <CANDIDATE_UUID> \
    --output-dataset-id pairwise-calibration-v0-dev-smoke \
    --output-dataset-version 1

# 3. 校验:loader 收得到,内容是 20-30 pair(条件下界)
#    实际输出文件:
#    evals/v2/datasets/pairwise-calibration-v0-dev-smoke.jsonl
#    evals/v2/datasets/manifest-pairwise-calibration-v0-dev-smoke-1.json

# 4. HTTP 端到端(需 dev JWT):
#    登录拿 token,i.e. POST /api/v1/auth/dev-login
#    POST /api/v1/eval/runs/{baseline_exp}/pairwise/run
#         body 含 fixture_mapping{pair_hash: {output-payload}} 让 FixtureJudge
#         返回 completed-winner(真实 LLM 场景 mapping 留空)。
#    GET  /api/v1/eval/runs/{baseline_exp}/pairwise/run/{sweep_id}
#         轮询直到 status ∈ {completed, failed, cancelled}。
#    GET  /api/v1/eval/runs/pairwise/pairs/{pair_id}/review-surface
#         拿 review_token(REQUIRED)。
#    POST /api/v1/eval/runs/pairwise/annotations   (token 必填)
#         两名 reviewer 各一次 primary。
#    POST /api/v1/eval/pairwise/calibration
#         sweep_ids=[...]

# 5. 期望断言(CI 测试覆盖不到,只有手动 run 才能算 Stage A closed):
#    response.calibration_status == "insufficient"
#    response.usage_mode        == "diagnostic_only"
#    response.report_payload.valid_human_pair_count       >= 1
#    response.report_payload.position_metric_sample_count >= 0
#    (>=100 才会晋升 passing/failing,但 v0-dev-smoke 总是 < 100。)
```

### 9.5 为什么 Commit 3.3 没有提交真实 dataset 文件

仅 `plumbing` 进 commit。真实 `pairwise-calibration-v0-dev-smoke.jsonl` + manifest 的 commit 留到下一会话,因为它们依赖具体 experiment UUID(各自环境不同),且产出属于 Stage A 的"执行"动作,不属于"工程能力"动作。提交阶段参考 §9.4 step 2 后:`git add evals/v2/datasets/pairwise-calibration-v0-dev-smoke.*`。

### 9.6 仍 BLOCKED 的门禁(原始声明)

```
PR-9c.2 engineering gate                 = PASS  (Commit 3.2 closed)
Stage A 端到端 smoke 验证               = PENDING  (Commit 3.3 落 plumbing,
                                                    实际 dataset 导出 +
                                                    HTTP 端到端 run 待手动执行)
PR-9 formal human-calibration gate       = BLOCKED
Overall PR-9                             = NOT CLOSED
```

### 9.7 命令快速验证基线(下一会话首跑)

```bash
cd "/Users/huanqi/Accompany Project/career-planning-buddy-ly"
git fetch && git checkout feat/eval && git pull
git rev-parse HEAD  # 期望 cced011...
git status --short  # 期望空
cd backend && /opt/anaconda3/envs/cp/bin/alembic current  # 期望 20260815_0016

cp .env .env.backup
sed -i.bak 's/^AGENT_FEATURE_STAGE=/#AGENT_FEATURE_STAGE=/; s/^AGENT_MAX_TOOL_ROUNDS=/#AGENT_MAX_TOOL_ROUNDS=/' .env
/opt/anaconda3/envs/cp/bin/python -m pytest tests/ -q
# 期望 496/496 passed
cd .. && mv .env.backup .env && rm -f .env.bak
```

---

## 10. PR-9c.2 Stage A — Fixture-backed E2E smoke CLOSED (Commit 3.4.x)

> Status: Stage A fixture E2E smoke = **PASS** (via service-layer path).
> Stage A real-data E2E = **BLOCKED_ZERODATA**.
> PR-9c.2 engineering gate remains PASS; PR-9 formal human-calibration gate remains BLOCKED.
> This section freezes the entire Stage A evidence trail (Steps 4 + 5 + 6 + provisioning + dataset + mapping) as a SINGLE evidence commit.

### 10.1 Stage A commit chain

```
039aa6b  Commit 3.4 — PairSmoke provider (Stage A E′) + step_a_precheck/pass
<NEW>    Commit 3.4.x — Stage A fixture-backed E2E smoke (Steps 4-6 + dataset + mapping + scripts fix + this §10)
```

### 10.2 Stage A artifacts in this commit

```
backend/evals/v2/datasets/pairwise-calibration-v0-dev-smoke.jsonl                  (21 rows, sha256 f51bbaa6...)
backend/evals/v2/datasets/manifest-pairwise-calibration-v0-dev-smoke-1.json
backend/evals/v2/datasets/pairwise-calibration-v0-dev-smoke.fixture_mapping.json   (21 entries, 7/8/4/2 distribution)
backend/scripts/export_pairwise_dataset.py                                         (manifest relative-path fix)
backend/scripts/stage_a_sweep.py                                                   (Step 4 driver)
backend/scripts/stage_a_review.py                                                  (Step 5 driver)
backend/scripts/stage_a_calibration.py                                             (Step 6 driver)
pr-9c-handoff.md §10                                                              (this section)
```

### 10.3 Provisioning rows persisted in DB (commit-transparent)

EvalExperiments + EvalTrials + EvalEvidenceItems + EvalTrialPair +
EvalPairwiseSweep + EvalPairwiseSweepItems + EvalPairwiseJudgeResults
+ EvalPairwiseHumanAnnotations + EvalPairwiseCalibrationReport — all
created by the Stage A run, all real DB rows, all queryable from the
production DB. They are NOT in the commit (DB artifacts belong to the
runtime, not the repo), but they are persisted and reproducible via:

```bash
PYTHONPATH=. python scripts/stage_a_provision.py   # idempotent within the PairSmoke experiment ids
PYTHONPATH=. python scripts/stage_a_precheck.py \
  --baseline-experiment-id 40491b69-... \
  --candidate-experiment-id 1ab11807-...
PYTHONPATH=. python scripts/export_pairwise_dataset.py \
  --baseline-experiment-id 40491b69-... \
  --candidate-experiment-id 1ab11807-... \
  --output-dataset-id pairwise-calibration-v0-dev-smoke \
  --output-dataset-version 1
PYTHONPATH=. python scripts/stage_a_sweep.py
PYTHONPATH=. python scripts/stage_a_review.py
PYTHONPATH=. python scripts/stage_a_calibration.py
```

Key identifiers:

```
stage-a-fixture-compact-v1   experiment id  40491b69-e2bf-4ef8-82af-d7be4ac82295
stage-a-fixture-structured-v1experiment id  1ab11807-3893-4798-8c51-713787afa4dc
pairwise-calibration-v0-dev-smoke dataset     (21 pairs, sha256 f51bbaa68b217cbea7211245c2694068116edffa96aed42f8c84a777249ae4f3)
fixture_mapping                              21 entries, sha256 fef1932b740e33acf6e6dd122852c88cb5403171f93e381593eb7595bd3d023a
pairwise Sweep id                            ec8c0693-da68-4f5f-874c-3c18c7b5ec76
Stage A smoke Calibration Report             26fdd054-0827-4836-9265-6b84c842e37b
```

### 10.4 Step 4 Sweep result

```
sweep_status                : completed
requested_pair_count        : 21            requested_judge_run_count  : 42
completed_judge_run_count   : 42            failed_judge_run_count     : 0
completed_pair_count        : 21            position_pair_count        : 21
items_total                 : 42            items_baseline / swapped   : 21 / 21
items_by_status             : {completed: 42}
judge_results_total         : 42            judge_results_invalid      : 0
fixture_mapping_size        : 21
```

### 10.5 Step 5 Human Review result

```
Case A (consensus):
  pair_id                : 718fc0b1-a411-4c30-8e27-31fd1cfdf0a8
  primary reviewers      : 2 (R1, R2)
  raw_winner (both)      : a   →  normalized = baseline
  consensus_normalized   : ["baseline"]  ← single value, consensus exists

Case B (disagreement + adjudication):
  pair_id                : d437d457-ba6f-4aea-8609-3cb9b05481a7
  primary R3             : raw a → normalized baseline
  primary R4             : raw b → normalized candidate
  adjudicator R5         : raw tie → normalized tie   (R5 ∉ {R3, R4})
  disagreement triggered : overall + all 5 dimensions
  pair lock              : SELECT FOR UPDATE engaged under submit_adjudication
  
Blinding audit           : forbidden tokens (trial_id, pair_hash,
                            experiment_id, baseline, candidate, model_id,
                            variant_role) absent from surfaced review payload
review_token shape       : 16 hex; per-(pair, reviewer) → tokens differ
frozen_sha               : deterministic per (pair, surface_version)
```

### 10.6 Step 6 Calibration Report result

```
smoke_calibration_report_id : 26fdd054-0827-4836-9265-6b84c842e37b
calibration_status          : insufficient       (matches expected)
usage_mode                  : diagnostic_only    (matches expected)
report_payload carries real metrics (NOT hard-default):
    valid_human_pair_count       : 2     (< 100 threshold)
    position_pair_count          : 21
    position_metric_sample_count : 21    (>= 100 threshold NEEDED for gate)
    agreement                    : 0.0   (judge labels do not match the
                                          smoke human labels — expected,
                                          see §10.7)
    position_bias                : null  (no decisive winner divergence
                                          across positions — expected,
                                          because fixture_mapping emits
                                          the same display-side verdict
                                          for both items of a pair)
    agreement_sample_count       : 2
```

Stage A audit (§6.1–6.5):

```
6_1 label completeness:
  total pairs in sweep       : 21
  pairs with any annotation  : 2      (smoke depth)
  pairs with two primaries   : 2
  pairs with adjudication    : 1
6_2 consensus rate (smoke, NOT statistical):
  consensus pairs            : 1 / 2 touched
6_3 disagreement:
  disagreement pairs         : 1 / 2 touched
  per_pair_labels            : {d437d457...: ["baseline", "candidate"]}
6_4 adjudication consistency:
  disagreement_without_adjudication : []    (all disagreements adjudicated) ✓
6_5 gold-label provenance:
  label_source_breakdown     : {consensus: 1, adjudication: 1}
  suggested_label_used_as_gold: false  ✓
```

### 10.7 Follow-up items ( PARKED, not actionable in Stage A )

```
FOLLOW-UP-1  ASGI transport lifecycle smoke test
              The committed code path is verified; the ASGI request-session
              commit lifecycle is not independently exercised by a Stage A
              smoke. Belongs in PR-10 (when CI-grade tests land).

FOLLOW-UP-2  position_variant in GET /review-surface payload
              The Commit 3.2 schema includes position_variant for
              programmatic clients. The literal token "baseline"/"swapped"
              trips the Stage-A blinding-audit token list. Long-term
              direction: split the audit list into (forbidden-identity
              tokens) vs (authoritative task fields), keeping
              position_variant permissible. Recorded as schema cleanup
              backlog; not blocking.

FOLLOW-UP-3  Stage A pair-row placeholder hashes
              EvalTrialPair rows carry placeholder pair_hash / input_hash
              (sha256 of trial-id pair). Downstream consumers (export →
              loader → judge) correctly recompute from frozen JSONL; the
              placeholder is fine, but a future pass could land real
              loader-formula hashes on the pair row for consistency.

FOLLOW-UP-4  Stage A repair-case failures (2 / 23 fail out)
              repair-03 + repair-04 trials in stage-a-fixture-compact-v1
              failed during provisioning (transient IntegrityError on
              duplicate step_projection during Interrupted grading). 21
              of 23 cases still graded cleanly — well inside the Stage A
              acceptance band. Root cause is in the grading path's
              partial-tolerance handling and worth its own ticket before
              PR-9 v1 / real-data E2E.
```

### 10.8 Triple-gate declaration (frozen)

```
Stage A fixture-backed E2E smoke             : PASS
  (Steps 1-6 green; service-path-verified,
   ASGI transport lifecycle parked as FOLLOW-UP-1)
Stage A real-data E2E smoke                  : BLOCKED — ZERODATA
  (PairSmoke-backed training data does NOT count as real; §8.8 boundary
   preserved — v0-dev-smoke is NOT v1 and never becomes v1. Real v1
   requires real live-LLM / real-graded Trials.)
PR-9c.2 engineering gate                     : PASS    (Commit 3.2 closed)
PR-9 formal human-calibration gate           : BLOCKED
  (needs ≥100 valid human primary pairs dual-reviewed / adjudicated
   against pairwise-calibration-v1, NOT v0-dev-smoke)
Overall PR-9                                 : NOT CLOSED
```

### 10.9 Disclosures framed per reviewer's §8 / §9 conditions

* This Stage A PASS proves **pipeline correctness**, not statistical
  reliability. 5 annotation rows out of 21 pairs touched = smoke.
* PairSmoke provider is a deterministic fixture; the produced
  baseline/candidate plan divergence is engineered, not learned. Real
  product divergence (live LLM) is the v1 / PR-9 gate's responsibility.
* `position_metric_sample_count == 21` looks like it satisfies the v1
  threshold of ≥ 100 — but it doesn't, because (a) the dataset itself
  is smoke (`v0-dev-smoke`, not `v1`), and (b) the threshold also
  requires `valid_human_pair_count >= 100` which this dataset will
  never reach.

---

END
