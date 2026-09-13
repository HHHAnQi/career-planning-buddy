# Harness 设计

Harness 是围绕 Agent Runtime 的工程控制，不是额外 Agent。

| 模块 | 作用 | Stage |
|---|---|---:|
| Events | SSE 持久化、sequence、终态唯一与续传 | 2 |
| Trace | Run/Step/Tool 记录 | 2 |
| Budget | LLM/Tool 次数、Deadline、取消检查 | 2 |
| Snapshot | graph/config/input 快照 | 2 |
| Finalizer | Run 终态只写一次 | 2 |
| Replay | 固定快照和 Tool fixture 重跑 | 5 |
| Eval | 固定数据集和 Grader | 5 |

- [总览](./harness-overview.md)
- [实现结构](./implementation-structure.md)
- [Replay](./replay.md)
- [Eval](./eval-system.md)
