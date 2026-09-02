# Career Planning Buddy v1 Release Verification

> Date: 2026-08-09  
> Checkout baseline: `0de0292484176752b528c021129bde0724d4157e` on
> `feat/stage6-memory-upgrade`  
> Rule: results below come from this checkout and this run, not old gap/handoff claims.

## Verdict

**B. 基本完整，但仍有明确阻塞问题。**

The repository is structurally complete and is demonstrable in deterministic portfolio
mode. Migration, contracts, tests, build, Eval V2, real Baidu Search, local BGE and the L3
knowledge path all passed. I am not assigning A because the configured real LLM was not
reachable in this run, so one successful end-to-end chain combining real LLM, confirmed L2,
real Baidu/L3 and a completed live Eval Trial was not obtained.

Remaining release-signoff blockers:

1. Restore/verify OpenAI-compatible provider reachability, then rerun the small real-provider
   create/review/confirm/replan flow and confirm the post-confirmation Plan actually references
   L2 memory.
2. Rerun one small live Eval after provider recovery and obtain a completed/scored Trial. The
   current attempt reached Report correctly but the Trial hit its deadline while the physical
   Provider call was pending.
3. Pairwise remains `diagnostic_only` until sufficient genuine human calibration exists. This
   is an honest product limitation, not permission to synthesize labels.

No failing deterministic check or known migration defect remains.

## Repository and scope controls

- Initial branch: `feat/stage6-memory-upgrade`.
- Initial HEAD: `0de0292` (`fix(eval): correct report gates and runtime failure accounting`).
- No merge, rebase, reset, commit or push was performed.
- `.env` remains ignored and untracked; its contents were never printed, copied or summarized.
- `docs/design-input` has no diff.
- No new Agent/RAG infrastructure, worker system or service boundary was added.
- Two test-only `.pytest_tmp_final*` directories created during Windows sandbox diagnosis were
  removed after their absolute paths were verified inside `backend`; they contained no source
  or user data.

## Automated acceptance evidence

### Docker and database

- `docker version`: server `28.4.0`.
- `docker compose --env-file .env.example config --quiet`: passed.
- PostgreSQL `pgvector/pgvector:pg16`: healthy on port 5432.
- `alembic heads`: one head, `20260816_0019`.
- `alembic current`: `20260816_0019 (head)`.
- The existing database was actually upgraded from `20260804_0007` through all intermediate
  revisions to `0019`; this was not inferred from filenames.

Migration `20260816_0019` adds the immutable Experiment identity fields `feature_stage`,
`search_version` and `eval_harness_version`, with legacy-safe defaults and an updated
immutability trigger/check. Historical snapshots remain schema-readable; old rows were not
bulk rewritten.

### Canonical check

`./scripts/check.ps1` completed successfully:

- Ruff: passed.
- Mypy: passed, 239 source files.
- Pytest: **572 passed** in 81.16 s.
- Legacy deterministic Eval: Stage 5 **30/30**, Stage 6 memory/context **12/12**.
- Eval V2 deterministic smoke: one Mock runtime case completed from Case to Report with one
  Trial, Grade data and no runtime/provider/configuration failure.
- Frontend Vitest: **7 files / 13 tests passed** after adding the dev-route guard regression.
- TypeScript + Vite production build: passed, 1,949 modules transformed.
- OpenAPI snapshot: regenerated from the application and passed its equality test.

The check scripts now force `APP_ENV=test` and Mock provider modes, so local credentials cannot
turn normal verification into paid/live calls. CI passes `${{ github.sha }}` as
`APP_GIT_COMMIT` and runs the same small deterministic V2 smoke.

## Product verification

A temporary hidden single-worker backend was started on port 8010 with explicit test/Mock
configuration and stopped immediately after the run. `scripts.e2e_demo` completed:

```text
Guest → Profile → create Plan → complete Task → Review → Replan
→ MemoryCandidate confirm → RAG retrieval
```

Observed results:

- create-plan: completed;
- task: completed;
- Review: `adjust`, replan suggested;
- replan: contractually degraded but returned a valid Plan;
- memory candidate: confirmed;
- seeded ExperienceAtom: returned by RAG at similarity 1.0.

The first attempt exposed an E2E fixture collision: repeated demos inserted identical atom text,
so a `limit=3` query could return older identical rows instead of the newly asserted ID. The
script now includes a unique deterministic-run marker; the rerun passed. This changes demo
isolation only, not production retrieval semantics.

Static and API tests cover loading/empty/error/degraded/cancelled paths, SSE persistence and
reconnect contracts, task/review/replan transitions and frontend auth redirects. No current
router dead end was found. The unreferenced HomePage and its meaningless test were removed;
root still enters Guest Login/Today through existing redirects.

## Agent Runtime

- Fixed LangGraph workflow, Provider protocols, budgets, deadlines, format repair, business
  repair, deterministic fallback and terminal-aware finalization remain intact.
- Cancellation tests cover Agent/Eval convergence; Eval cancellation now explicitly catches
  `CancelledError` and atomically cancels nonterminal Trials and the Experiment, idempotently.
- Run snapshots for new work identify `stage6b-v1`, feature stage 6 and canonical prompt/tool/
  context/memory/search versions. Stage 5 historical snapshots still validate.
- Real LLM smoke recognized the configured OpenAI-compatible mode. With a local-only JWT secret
  supplied for the rollback-only smoke, create-plan failed as `PROVIDER_UNAVAILABLE` in the
  planning node. There was no silent Mock fallback and no credential was emitted.

Conclusion: true-model wiring is present and failure-safe, but current real-provider
reachability prevents an A-level live signoff.

## Three-layer memory and Search/RAG

### L1 Working Memory

Deterministic compression, untrusted-section prompt isolation, context budgets and
RunInputSnapshot behavior pass their regression suites. New snapshots freeze the current
Stage 6 identity.

### L2 Personal Episodic Memory

Tests verify Review → candidate, explicit confirm/reject, inactive/unconfirmed exclusion,
user isolation, pinned priority, semantic/recency selection, context limits, fallback and
evidence/`last_used_at` updates. The HTTP E2E confirmed a candidate. A successful real-LLM
post-confirmation replan was blocked by external provider reachability, so that precise live
assertion remains a signoff item.

### L3 Semantic Knowledge Memory

The real Stage 6B chain succeeded using the configured providers:

- Baidu returned 10 results across two small queries;
- 10 SearchSources were persisted without fabricated Mock rows;
- 3 ExperienceAtomCandidates were distilled;
- one candidate was developer-approved;
- local BGE produced pgvector results, 5 RAG hits, top score 1.0.

A follow-up Plan validation completed and contained 3 approved `experience_atom` evidence
references. Thus real SearchSource → review → ExperienceAtom → BGE/pgvector → RAG → Plan
evidence is verified. The newest atom was not necessarily one of the top selected rows in a
database containing older approved knowledge; the assertion correctly requires approved L3
evidence, not a fabricated ranking guarantee.

Baidu hostname classification is now fail-closed with IDNA normalization and exact/dotted
subdomain matching. Malicious similar domains are regression-tested. Baidu failure never
falls back to Mock.

## Eval Harness V2

- API, CLI and Snapshot use one canonical runtime identity. Explicit `APP_GIT_COMMIT` wins;
  Git lookup is bounded/best-effort and returns `unknown` rather than `0000000` when unavailable.
- API candidate creation now preserves `baseline_experiment_id`, `variant_role` and
  `agent_variant`; list/status expose dataset and complete version identity.
- The live-only wrapper order is Retry/Pacing(Audit(real)), so each physical attempt can be
  persisted with its `retry_attempt`.
- Fake-provider tests pass for 429/timeout/5xx recovery, 401/schema/nonretryable failures,
  max attempts, `Retry-After`, deadline, cancellation during backoff and concurrency/pacing.
- Reports distinguish `model_failure`, `provider_transient_failure`,
  `provider_exhausted_after_retry`, `cancelled`, `configuration_error` and `internal_error`.

The one-case live CLI attempt created and finalized an Experiment/Report but did not complete
the Trial: it ended `AGENT_DEADLINE_EXCEEDED` after 6.418 s and was correctly counted as one
runtime/internal failure, not an Agent quality score. Its single physical ProviderCall was
persisted as cancelled at retry attempt 0 because the deadline interrupted the call before a
transient response existed to retry. This is correct bounded behavior, but not a successful
live quality sample.

Pairwise/Calibration functionality and APIs pass tests. No artificial human labels were
added; release claims remain diagnostic.

## Frontend, API and authorization

- `/dev/runs` now uses the shared authenticated API client and no longer asks users to paste a
  second JWT.
- New `/dev/evals` supports list, small Mock/fixture create, status/progress polling, cancel,
  report/failure/token summary, baseline/variant/version identity and calibration status.
- The browser does not expose a one-click paid live run.
- Both developer routes are only linked for `me.user.role === "dev"`; backend `require_dev`
  remains mandatory and tested. No role-promotion HTTP endpoint was added.
- Backend strict schemas, checked OpenAPI and frontend types agree on Eval status/list fields,
  cancellation, pagination, baseline/variant and runtime identity.

## Security and privacy review

- JWT identity is claim-derived; developer endpoints verify role server-side.
- SSE auth remains header-based, not query-string based.
- Provider secrets are `SecretStr`, server-only and absent from the frontend API/bundle,
  ProviderCall projections, snapshots and stable error responses.
- Test/CI settings explicitly ignore environment files when `APP_ENV=test`, preventing local
  provider credentials from contaminating deterministic checks.
- L2 consent/user isolation and L3 real-source/developer-approval boundaries have passing
  regression coverage. Guest approval of global knowledge is not exposed.

## Documentation verification

README is now the standalone entry point for system positioning, architecture, three memory
layers, provider modes, Safe Mock/local/Docker startup, developer surfaces, Eval V2 and honest
Pairwise limitations. `docs/architecture/current-system-overview.md` is the canonical detailed
current-state document. The node index and distillation specs match `ExperienceAtomService`;
old Eval/handoff records are visibly marked historical/superseded.

## Explicit non-goals retained

This release does not add multi-worker scheduling, Redis/Celery, Kubernetes, microservices,
MCP, multi-agent execution, a webpage crawler, hybrid RAG/reranking, GPU Compose, a large admin
dashboard, large paid Eval or synthetic “human” labels. These remain deliberate v1 scope
limits rather than hidden gaps.

## Path to rating A

No code expansion is required. In an environment where the configured LLM endpoint is
reachable, rerun:

1. the rollback-only real-provider product smoke through post-confirmation replanning;
2. one case of Eval V2 live with the fixed identity and bounded retry settings;
3. verify the resulting L2 evidence reference and completed live Trial/ProviderCall audit.

If those pass without changing to Mock, the present deterministic, database, real Baidu/BGE,
frontend and documentation evidence is otherwise sufficient for the requested v1.0 Release
Candidate signoff.
