# Eval Live Validation - 2026-08-08

> **Historical implementation note.** This is evidence from a small live run on
> 2026-08-08, not the current system specification and not a quality superiority claim.
> See [`../../README.md`](../../README.md) and
> [`../architecture/current-system-overview.md`](../architecture/current-system-overview.md)
> for current behavior. The bounded Eval-only retry/backoff/pacing layer requested by
> this run's findings is now implemented; human calibration remains insufficient for a
> quality gate, so Pairwise results remain diagnostic.

## Scope

This record validates the local Eval V2 path with real providers:

- Agent model: GLM `glm-4.7` through the Zhipu OpenAI-compatible API.
- Judge model: DeepSeek `deepseek-v4-pro` through a separate API account.
- Database: the project's local PostgreSQL container.
- Comparison: B0 `direct_llm_v1` versus B3 `full_agent_v1`.
- No API key or raw model response is recorded in this document.

The run is a smoke/acceptance sample. It is not statistically sufficient to
claim that either variant is better.

## Harness Changes Validated

- B0 uses the common Experiment, Trial, Run, evidence, grader, and report path.
- B0 receives explicit request/profile/planning-window state only.
- B0 run snapshots freeze `available_tools=[]` and
  `career_planning=direct_llm_baseline_v1`.
- B3 run snapshots retain `memory_lookup`, `rag_retrieve`, and `web_search` and
  use `openai_compatible_plan_stage6_context_v1`.
- Pairwise Judge runs use deterministic IDs and both baseline/swapped display
  positions.
- A Judge comparison is skipped unless both Trials completed.
- Provider audit now persists an error row and re-raises the original
  `AgentError`, preserving `PROVIDER_TIMEOUT` across Run and Trial reporting.
- Interrupted Eval recovery now has an automated PostgreSQL regression test.

## Provider Configuration Findings

- GLM needed a 90-second HTTP timeout. At 60 seconds, two B3 calls were
  correctly audited as `PROVIDER_TIMEOUT`; the old audit wrapper then swallowed
  the exception and caused a misleading `STRUCTURED_OUTPUT_INVALID` result.
- DeepSeek reasoning exhausted 800 and 2000 output-token budgets before
  producing `content`. A 4096-token budget completed both position-balanced
  Judge calls.
- The safe empty-content diagnostic records only finish reason, whether a
  reasoning field exists, and completion-token count. It never records hidden
  reasoning or raw response content.

## Recorded Experiments

### Exploratory run

- B0 experiment: `57c82202-89f2-4996-891f-101158ac33ea`
- B3 experiment: `9a6b4a27-f97b-49af-b763-512765d133d9`
- Cases: `create-01`, `replan-01`
- Outcome: two of four Trials completed. The two failures exposed the timeout
  propagation defect described above. One completed Judge result was persisted
  before a second Judge call returned empty content.

### Final valid pair

- B0 experiment: `7669abcd-260c-4ba4-9b08-5a521dad83f3`
- B3 experiment: `bdf08fc4-2d15-440a-8229-068ff8e265f1`
- Case: `create-02` (`Build an internship preparation plan`)

| Metric | B0 Direct LLM | B3 Full Agent |
|---|---:|---:|
| Trial status | completed | completed |
| Latency | 31,195 ms | 20,641 ms |
| Input tokens | 1,165 | 1,916 |
| Output tokens | 2,432 | 748 |
| Total tokens | 3,597 | 2,664 |
| Structured output gate | pass | pass |
| Time budget gate | pass | pass |
| Safety gates | pass | pass |
| Task startability gate | **fail** | **fail** |

Both variants generated all 33 configured deterministic score rows. The
`task.startability` hard gate failed for both, so this case does not pass the
overall hard-gate release condition.

### Post-validation correction

The shared `task.startability` failure was later confirmed to be a Harness
collector defect, not a model-output defect. Both persisted tasks contain a
non-empty `starter_action`, but `_task_projection` omitted that field before
the Task Grader read the frozen outcome. The collector now projects
`starter_action`, and a database-bound regression test verifies both the
evidence field and the final `task.startability` pass result. The historical
Score rows above remain immutable; use a new Experiment for subsequent quality
comparisons rather than interpreting these two failed rows as model quality.

## Pairwise Judge

Pair ID: `820f0ffd-f0dd-48d8-aa6a-b44e17bb5fb7`

| Display position | Normalized verdict | Confidence | Latency |
|---|---|---|---:|
| baseline | tie | medium | 11,703 ms |
| swapped | tie | high | 17,909 ms |

The normalized verdict is stable after swapping. This validates position
normalization for the sample. It does not establish Judge calibration or model
superiority.

## Automated Verification

- Focused B0/provider tests: 16 passed.
- Focused Judge/provider tests after final changes: 40 passed, 1 unrelated
  database-bound test deselected in the host-only run.
- Docker database integration set: 24 passed, including interrupted Eval
  recovery.
- Full backend suite: 541 passed, 1 failed.
- Type checking: 164 source files passed.
- All files changed in this work pass Ruff.

The one full-suite failure is the pre-existing OpenAPI snapshot drift around
generated 422 response descriptions. Full-repository Ruff still reports three
pre-existing findings: two redundant f-string prefixes in migration `0012` and
one unused import in `stage_b1_provision.py`.

## Acceptance Decision

**Pass for campus-project Eval harness execution readiness.** The project can
run, persist, audit, deterministically grade, and independently blind-judge a
real B0/B3 pair.

**Not yet passed for comparative quality claims.** Before presenting a result
such as "Full Agent is better than Direct LLM", run the frozen 30-case Golden
Set (or at minimum a balanced 10-case demonstration set), investigate the
shared `task.startability` failure, and perform a small human spot check of
Judge labels.

## Security Note

The two API keys were supplied in chat text. Rotate both keys after this
validation and update the ignored local `.env`; never commit the current keys.

## Hard-Gate Semantics Correction

The report aggregation previously treated an EvalScore row's `hard_gate`
classification as if it were the row's pass verdict. This could reject a Trial
merely because it contained advisory metrics, and could accept a failed hard
gate merely because that row was configured as a gate.

The corrected rule is:

```text
score gate requirement passed = not hard_gate or passed is true
trial passed = every persisted score gate requirement passed
```

Both the immediate ExperimentRunner report and the database-backed report
rebuild now use the same rule. Regression coverage includes a passing hard
gate, a failed hard gate, and a failed advisory metric that must remain neutral.

Current automated verification after the correction:

- Eval and Eval API tests: 394 passed.
- Full backend suite: 549 passed.
- Ruff: all checks passed.
- Mypy strict: 234 source files passed.

## Three-Case Live Debug Run

This run was performed after the hard-gate correction. It is a stability and
diagnostic run, not a comparative-quality acceptance run.

- B0 experiment: `b3f7035f-99a1-4df4-b44d-0f289762b466`
- B3 experiment: `f51425d2-ec7d-4cc9-aebe-b30a1f2a6d4f`
- Cases: `create-01`, `create-03`, `replan-03`
- Agent: GLM `glm-4.7`
- Judge: DeepSeek `deepseek-v4-pro`
- Search and embedding providers remained explicitly mock-scoped.

| Case | B0 result | B3 result | Position-balanced Judge |
|---|---|---|---|
| `create-01` | pass; 28,935 ms; 2,620 tokens | pass; 15,968 ms; 2,647 tokens | B0 / B0; stable |
| `create-03` | pass; 25,038 ms; 2,670 tokens | pass; 19,424 ms; 2,739 tokens | both unacceptable / B0; unstable |
| `replan-03` | degraded and gate-failed; 81,411 ms; 7,124 tokens | failed at 90,197 ms | skipped |

For `replan-03`, B0 exhausted business-rule repair and failed
`behavioral.graph_branch`, `model.structured_output`, and
`task.allowed_run_status`. B3 timed out in `career_planning_agent` with
`AGENT_DEADLINE_EXCEEDED`; no ToolCall row was persisted for that Trial.

### Debug Decision

Do not expand directly to the balanced 10-case acceptance run yet. First:

1. reproduce and diagnose the B3 `replan-03` node timeout;
2. rerun the affected pair after the runtime fix or bounded-timeout decision;
3. inspect the two `create-03` Judge outputs before accepting its label;
4. proceed to 10 cases only when all selected pairs complete and position
   instability is explicitly flagged rather than treated as a quality win.

### Timeout Follow-Up

The first `replan-03` rerun froze a 180-second Run deadline but still ended at
90 seconds. The frozen node configuration revealed that real-provider
`career_planning_agent` was independently capped at 90 seconds, even though
the node may perform an initial call plus bounded format repair. Cancellation
audit also attempted to store `status=cancelled` together with a non-null
`error_code`, contradicting the database contract and dropping the audit row.

The runtime was corrected so real LLM nodes use the frozen Run deadline as
their ceiling while each physical provider call retains its own HTTP timeout.
Cancelled provider calls now persist `status=cancelled` with a null error code,
then re-raise cancellation unchanged.

Post-fix `replan-03` rerun:

- B0 experiment: `ac763b56-1b7e-4684-a38c-57a736c6a50a`
- B3 experiment: `eea70909-72bb-4526-a9a7-734255ca6154`
- B0: degraded, hard-gate fail, 68,126 ms.
- B3: completed, all hard gates passed, 14,113 ms.
- Judge: B3 / B3 across baseline and swapped positions; stable.

## Balanced Ten-Case Acceptance Run

The acceptance set contained five create cases and five replan cases:
`create-01` through `create-05`, and `replan-01` through `replan-05`.
The Run deadline was frozen at 180 seconds.

- B0 experiment: `1135049c-5883-4c48-a6e0-5faf05a7f4e3`
- B3 experiment: `6e81f139-f56f-4437-9e49-1e2f146db52d`

| Outcome | B0 Direct LLM | B3 Full Agent |
|---|---:|---:|
| Trials | 10 | 10 |
| Completed | 5 | 2 |
| Runtime failures | 5 | 8 |
| Hard-gate passes | 4 | 2 |
| Overall success rate | 40% | 20% |
| Wilson 95% CI | 16.8%-68.7% | 5.7%-51.0% |

B0 runtime failures comprised four `PROVIDER_TIMEOUT` results and one
`AGENT_BUDGET_EXCEEDED`. B3 runtime failures comprised two
`PROVIDER_TIMEOUT` results and six `PROVIDER_RATE_LIMITED` results.

Only `create-01` completed in both arms. Its position-balanced Judge outputs
were `tie` and B3, so the pair was position-unstable. The other nine Judge
comparisons were correctly skipped because both Trials had not completed.

### Acceptance Decision

**Fail for comparative-quality acceptance.** The run cannot support a B0-vs-B3
quality claim because provider timeouts and rate limiting dominate the sample,
and only one pair was comparable.

**Valid reliability finding.** The Harness preserved every Trial status,
provider failure category, deterministic score, skipped-Judge reason, and
report reconstruction input. It also exposed a second aggregation defect:
runtime failures were counted in a separate bucket but omitted from
`success_rate` and `hard_gate_pass_fraction` denominators. Both metrics now use
completed Trials plus runtime failures, while configuration failures and user
cancellations remain separate. Database-backed report reconstruction confirms
B0 at 40% and B3 at 20%; the former misleading values were 80% and 100%.

Search and embedding remained mock-scoped throughout this acceptance run.
Human Judge spot-checking remains deferred because there is not yet a viable
set of 5-10 completed, position-stable real pairs.

Before rerunning acceptance:

1. define and freeze a provider retry/backoff and pacing policy for live Eval;
2. allow the GLM account's rate-limit window to recover;
3. rerun in paced batches while preserving one frozen acceptance manifest;
4. require at least 8 of 10 pairs to complete before quality comparison;
5. send 5-10 completed pairs to human spot-check only after that gate passes.
