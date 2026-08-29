# Developer Run API

全部端点要求 role=dev。

## GET /api/v1/dev/runs

过滤：status、intent、result_kind、graph_version、model_id、created_from/to、cursor、limit。

摘要返回：run_id、脱敏 user id、status、result_kind、intent、graph version、model、token、cost、latency、fallback、created_at。

## GET /api/v1/dev/runs/{run_id}

返回：

- Run 元数据、result_kind 和终态 payload 摘要；
- graph/config/input snapshot 的脱敏视图与 hash；
- agent_steps；
- tool_calls 的脱敏参数、contract version、fixture 是否存在和结果摘要；
- agent_events 与 terminal event 检查；
- final PlanDetail（存在时）；
- 不返回 API Key、Authorization Header、未脱敏完整 Prompt 和完整网页正文。

## POST /api/v1/dev/runs/{run_id}/replay

R2 语义：对 `resume_optimization` Run 创建独立 Replay Run，使用冻结 input/runtime
bundle 重新执行 Graph。默认使用 Tool 与 Provider fixture；缺少任一必需 fixture 时失败，绝不
静默访问网络。其他 Run kind 在完成相同契约前返回 `REPLAY_KIND_UNSUPPORTED`。

Stage 5 客户端仍可提交 `{"tool_mode":"fixture|live"}`，但该请求只执行明确标记为
`legacy_trace_clone` 的兼容操作。新客户端使用 `mode=exact_fixture_replay` 或
`mode=candidate_comparison`；两者执行真实 Agent 图，不把 Trace 克隆伪装成 Replay。

```json
{
  "run_id": "...",
  "replay_of_run_id": "...",
  "status": "pending",
  "deterministic": true,
  "execution_kind": "exact_fixture_replay"
}
```

```json
{
  "mode": "exact_fixture_replay",
  "target_runtime_bundle_id": null
}
```

字段：

- `exact_fixture_replay`：使用源 Run 的 Tool/Provider fixture 与 runtime bundle；
- `candidate_comparison`：冻结源 input 和 Tool fixture，调用当前服务端 Provider；目标 bundle
  必须是服务端当前生效的 Runtime Bundle，响应必须标记 `deterministic=false`；
- Replay 使用原 input snapshot，不读取当前用户画像；
- 原 Run 必须处于终态；
- 新 Run 写 replay_of_run_id，不修改原 Plan 和原 Trace。

## GET /api/v1/dev/runs/{run_id}/replay-diff

`run_id` 必须是已完成的 Replay Run。响应比较业务语义而非数据库生成 ID，至少包含 Context、
Tool、Claim、Validation、Usage 五类 diff，并返回 `semantic_equal` 与 comparison version。
