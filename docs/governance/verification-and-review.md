# 验证与评审

## 验证金字塔

1. 单元测试：状态转换、规则校验、Schema、预算计算。
2. Repository/Service 集成测试：真实 PostgreSQL、事务、唯一约束、用户隔离。
3. API 契约测试：状态码、错误码、OpenAPI 快照、SSE 事件顺序。
4. Agent 纵切测试：Mock Provider 下完整 Run。
5. Eval：真实或固定 Provider 下评估计划质量与安全分流。
6. Demo 冒烟：Docker Compose 中走完整业务闭环。

## 评审重点

- 契约是否存在两个不同版本；
- 状态是否由客户端直接决定；
- Agent 节点是否直接操作 ORM；
- Tool 或 LLM 是否缺少超时与预算；
- 失败时是否仍写成 completed；
- SSE 断线重连是否有持久事件源；
- 指标是否来自真实测试而非设计目标。

## 结果记录

每个阶段在 Pull Request 中记录：执行命令、通过数量、失败项、已知限制和下一阶段阻塞。不得把未执行的命令写成“已通过”。
