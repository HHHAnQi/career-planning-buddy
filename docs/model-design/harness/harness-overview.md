# Harness 总览

Harness 是 Agent Runtime 的工程控制层，不是额外 Agent。

## 在线控制

```text
BudgetGuard
  - stage2/3 max_llm_calls=5
  - stage4 max_llm_calls=7；stage5 online reviewer enabled max_llm_calls=8
  - max_tool_rounds=2
  - max_tool_calls=4
  - max_total_tokens=16000
  - max_input_tokens_per_call=6000
  - max_output_tokens_per_call=1500
  - deadline=45s
  - tool_timeout=8s

SnapshotService
  - graph/config snapshot at run creation
  - input snapshot after context_builder

TraceRecorder
  - agent_runs
  - agent_steps
  - tool_calls + replay-safe result fixture

EventRecorder
  - agent_events
  - monotonic sequence
  - terminal event uniqueness
  - SSE replay

AgentRunFinalizer
  - completed/degraded/failed/cancelled exactly once
```

## 离线反馈

```text
JSONL Dataset → Eval Runner → Rule Graders → Report → Bad Case
Saved Input/Config Snapshot + Tool Fixture → Replay → Diff
```

## 原则

- 每次外部调用可归因；
- 每个终态可解释且只有一个 terminal event；
- Replay 不读取已变化的用户当前画像；
- 不保存 API Key 和未脱敏敏感 Prompt；
- 评测失败不自动篡改 Prompt；
- Replay 缺 Tool fixture 时默认失败，不伪装确定性；
- quality_reviewer 默认由 Eval/Replay 离线 shadow，不写原 Run 事件；online enforce 仅用于实验。
