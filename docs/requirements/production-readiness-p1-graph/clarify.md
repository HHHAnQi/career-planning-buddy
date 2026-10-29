# P1-2 Agent Graph 纯逻辑边界：需求澄清

## 问题

`backend/app/agent/graph.py` 同时承担 Graph 拓扑、I/O 编排、checkpoint 编解码、
Provider usage 解析与遥测组装。这些纯转换逻辑被困在大型编排类中，单元测试
需要绕过过多依赖，也容易在后续修改中把状态副作用混入转换过程。

## 本轮目标

- 提取不访问数据库、Provider、Repository 的运行时转换逻辑；
- checkpoint 恢复先完整校验，再由 Graph 显式写入状态；
- 保持节点名、拓扑、Provider 调用次数、事件和快照格式不变。

## 非目标

- 不调整 LangGraph 拓扑或并行策略；
- 不改数据库 Schema、HTTP API 或 Prompt；
- 不引入新的队列、缓存或微服务。
