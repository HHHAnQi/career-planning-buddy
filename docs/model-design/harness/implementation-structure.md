# Harness 代码结构

```text
backend/app/harness/
├── budget.py
├── events.py
├── trace.py
├── snapshots.py
├── finalizer.py
├── redaction.py
└── replay.py

backend/evals/
├── datasets/
│   └── career_plan_v1.jsonl
├── graders/
├── fixtures/
├── runner.py
└── report.py
```

## 调用位置

- `NodeRunner` 负责 step trace、节点事件和预算检查；
- `ToolRegistry` 负责 Tool 白名单、预算、trace 与 replay-safe fixture；
- `AgentRunExecutor` 负责加载 Run、构建 Graph、CancellationToken 与 Deadline；
- `AgentRunFinalizer` 负责所有唯一终态；正常 plan 通过 finalize_plan 在同一事务中调用 Persist Service；
- `EventRecorder` 负责持久化并通知 SSE，heartbeat 不持久化；
- `SnapshotService` 负责 graph/config/input snapshot；
- `Redactor` 被 Trace、Snapshot 和 Tool Fixture 共用；
- Eval Runner 使用独立 Eval 用户/事务与 Mock 或指定 Provider，不读取生产用户数据。
