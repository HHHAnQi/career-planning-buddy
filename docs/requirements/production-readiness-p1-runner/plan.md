# P1-4 Career Planning Runner：实施计划

1. 提取主 Agent 三轮有界循环。
2. 提取确定性 Memory 预执行、并行 Tool 执行和 evidence 去重。
3. 提取格式修复和 schema 失败降级。
4. Graph 的 checkpoint wrapper 调用 Runner，不改快照格式。
5. 增加架构守卫，防止 Provider/Tool 主循环回流 Graph。

## 验收

- Ruff、Mypy 通过；
- Agent Runtime、Stage 4 Tool、并行 Tool、Streaming、Checkpoint 回归通过；
- 全量 CI 通过；
- 无 API、迁移或新运行时依赖。
