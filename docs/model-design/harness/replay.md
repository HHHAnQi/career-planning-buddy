# Replay R2 契约

`resume_optimization` 已进入真实重执行边界：Replay 必须创建独立 Run，使用冻结输入、不可变
Runtime Bundle 和完整 fixture 重新执行 Graph，并在终态后持久化语义 diff。复制 Trace 的
`legacy_trace_clone` 只保留为历史内部实现，不再由 HTTP 入口使用。

## 默认输入

Replay 新建独立 Run，并使用原 Run 保存的：

- `input_snapshot_json`；
- `runtime_bundle_id` 与 bundle hash；
- `tool_calls.result_json` fixture。

精确 fixture replay 还必须冻结 Provider response fixture。只冻结 Tool、不冻结 LLM 响应时，
不得标记 deterministic。

Candidate Comparison 只复用源 Tool fixture，Provider 使用当前进程配置重新执行；服务端校验目标
Runtime Bundle 必须等于当前生效 bundle，避免把任意历史 bundle 与当前 Provider 错配。

可变项：model、prompt_version、quality reviewer 模式。默认不读取用户当前 Profile/Plan/Memory，避免历史输入漂移。

## Tool Fixture

Fixture key：

```text
tool_name + args_hash + tool_contract_version
```

缺 fixture 时：

- deterministic 模式：失败并返回 `REPLAY_FIXTURE_MISSING`；
- live 模式：仅开发者显式选择时访问 Provider，并标记 `non_deterministic=true`；
- 不允许静默回退到真实网络。

## 输出对比

至少对比并忽略 run_id、assessment_id、时间戳等生成字段：

- resolved_intent；
- result_kind/status/fallback_reason；
- Context 入选/排除与真实 Token；
- Tool 参数、输出及消费关系；
- Claim verdict/rewrite/requirement/evidence；
- Validation 与 source integrity；
- token、cost、latency；
- 节点和 Tool 调用差异。

Replay 不修改原 Run、原 Plan、原 Trace。默认只保存实验结果，不替换用户当前计划。

## V2 完成条件

只有同时满足以下条件，才可把执行种类标记为 deterministic Replay：从不可变 input/runtime
bundle 重建上下文；按 fixture contract 执行 Tool 和 Provider；重新运行 Graph；生成独立
结果；对新旧输出、规则、成本和 Trace 做语义 diff。复制旧结果不属于 Replay。

## Eval Harness V2 编排入口

Eval 控制面通过 `run_type=fixture_replay` 和必填的
`fixture_source_experiment_id` 创建回放实验。创建阶段按
`case_id + trial_index + variant` 将每个新 Trial 绑定到一个已完成、同数据集、
非回放且拥有唯一 Fixture Bundle 的源 Trial，并把绑定持久化到
`eval_trials.fixture_source_trial_id`。ExperimentRunner 只读取该冻结绑定，不在执行时猜测来源。

CLI 等价参数为：

```text
python -m evals.v2 run --provider-mode fixture --run-type fixture_replay \
  --fixture-source-experiment-id <uuid>
```
