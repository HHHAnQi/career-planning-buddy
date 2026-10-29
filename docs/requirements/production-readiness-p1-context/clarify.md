# P1-3 规划上下文端口：需求澄清

## 问题

`FixedPlanningGraph` 的 `memory_loader` 和 `evidence_loader` 直接创建 Repository、
查询 ORM 对象并转换 DTO。这让 Graph 同时承担拓扑编排和持久化细节，
与“Agent 节点不直接依赖 ORM”的基线不一致。

## 目标

- Agent 层仅定义只读 `PlanningContextPort`和可序列化 DTO；
- Service 层负责用户隔离查询、数据完整性校验和 ORM 到 DTO 映射；
- Graph 仍负责并行 fan-out、执行锁、预算、Trace、压缩与快照。

## 非目标

- 不修改 SQL 查询条件、上限或排序；
- 不改 LangGraph 拓扑、节点名和事件顺序；
- 不改 API、Schema、数据库或 Provider 调用策略。
