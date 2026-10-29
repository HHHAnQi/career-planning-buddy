# P1-4 Career Planning Runner：需求澄清

## 问题

`FixedPlanningGraph` 同时管理拓扑和主 Agent 的 Provider/Tool 循环，包括预算、
格式修复、证据去重、流式进度和模板降级。这使 Graph 难以作为纯编排器审查。

## 目标

- 独立 `CareerPlanningAgentRunner` 承担有界的 Provider/Tool 循环；
- Graph 保留 checkpoint、NodeRunner 和状态回填；
- 保持 Tool 提交顺序、并行方式、证据可见性、降级码和遥测字段不变。

## 非目标

- 不拆分业务规则修复节点；
- 不改 Prompt、Tool 契约、Graph 拓扑、API 或数据库。
