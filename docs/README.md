# Career Planning Buddy 文档索引

本目录是独立求职规划项目的实现依据，不依赖 ClawAgent。

## 最短阅读路径

准备开始编码时，只需按顺序读：

1. [项目实现基线](./implementation/project-baseline.md)
2. [阶段任务书](./implementation/README.md)
3. [TDD 技术设计](./architecture/tdd.md)
4. [API 与数据契约](./architecture/api-and-data-contracts.md)
5. 当前功能对应的 [model-design](./model-design/README.md)

## 目录职责

| 目录 | 用途 | 权威级别 |
|---|---|---|
| `implementation/` | 给编码助手直接执行的阶段任务与验收标准 | 最高 |
| `architecture/` | 技术选型、分层、运行时和跨模块协议 | 高 |
| `model-design/` | API、表、状态机、Agent 节点施工 spec | 高 |
| `overview/` | 产品背景、用户旅程和验收目标 | 中 |
| `standards/` | 编码、安全、测试和 Prompt 规范 | 中 |
| `governance/` | 协作和交付流程 | 中 |
| `third-party-integration/` | 外部模型、搜索和向量服务接入说明 | 中 |
| `requirements/` | 某次需求的 clarify / plan / tasks 留痕 | 任务级 |
| `design-input/` | 早期原始材料 | 只读归档 |

## 实现约束摘要

- 独立 FastAPI 单体，不基于 ClawAgent；
- 单核心 Agent + 受控节点，不做多 Agent；
- PostgreSQL + pgvector，MVP 不用 Redis；
- Provider 只保留 LLM、Search、Embedding 三类；
- Codex 用于阅读规范、修改代码和执行测试；项目运行时模型由 Provider 配置决定；
- Agent Run 使用数据库持久化事件 + SSE；
- Agent Run 使用 PostgreSQL lease 接管；Eval/Pairwise 仍是单 Worker 有限能力；
- 先跑通 Mock 纵切，再接真实模型。

## 关键入口

- [产品概览](./overview/product-overview.md)
- [需求规格 SRS](./overview/srs.md)
- [架构决策 ADR](./architecture/adr.md)
- [技术设计 TDD](./architecture/tdd.md)
- [API 与数据契约](./architecture/api-and-data-contracts.md)
- [数据模型](./model-design/data-models/README.md)
- [API 端点](./model-design/api-spec/README.md)
- [Agent Runtime](./model-design/agent-runtime/README.md)
- [Agent 节点](./model-design/agent-nodes/README.md)
- [Agent Tool](./model-design/tools/README.md)
- [Provider 配置与部署](./third-party-integration/provider-configuration.md)
- [生产就绪审查与演进边界](./review/production-readiness-audit-2026-08-10.md)
- [端到端运行链路](./model-design/end-to-end-runtime-flow.md)
- [Eval Harness 运行与可靠性边界](./implementation/eval-operations-boundary.md)
- [2026-08-09 项目加固与面试复习](./review/job-search-project-review-2026-08-09.md)
- [本轮审查报告](./review/revision-report.md)
