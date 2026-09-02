# Career Planning Buddy 文档中心

这里保存产品设计、当前架构、接口契约、实现任务和验证记录。首页只介绍如何理解与运行项目；需要继续阅读时，按下面的受众路径进入即可。

## 推荐阅读路径

### 产品与功能

1. [项目首页](../README.md)
2. [当前系统全景](./architecture/current-system-overview.md)
3. [产品概览](./overview/product-overview.md)
4. [5 分钟演示脚本](./overview/demo-walkthrough.md)
5. [用户使用说明](./overview/user-manual.md)

### 开发者

1. [项目实现基线](./implementation/project-baseline.md)
2. [TDD 技术设计](./architecture/tdd.md)
3. [API 与数据契约](./architecture/api-and-data-contracts.md)
4. [Agent Runtime](./model-design/agent-runtime/README.md)
5. [Agent Tool](./model-design/tools/README.md)
6. 当前功能对应的 API、数据模型、状态机和节点 Spec

### Eval / Agent 工程方向

1. [Harness 总览](./model-design/harness/README.md)
2. [运行与可靠性边界](./implementation/eval-operations-boundary.md)
3. [LLM Provider 与调用观测](./architecture/llm-provider-and-telemetry.md)
4. [Provider 配置与部署](./third-party-integration/provider-configuration.md)

## 文档状态

| 类别 | 目录 | 用途 |
|---|---|---|
| 当前事实 | `architecture/current-system-overview.md`、代码、迁移 | 描述当前已经存在的行为和限制 |
| 产品说明 | `overview/` | 当前用户价值、使用方式和演示路径 |
| 实现契约 | `model-design/`、`standards/` | API、数据、状态机、Agent 节点和编码规范 |
| 实施任务 | `implementation/` | 分阶段实现范围与验收标准 |
| 工程治理 | `governance/` | 开发流程、审查和交付定义 |
| 第三方接入 | `third-party-integration/` | LLM、Search、Embedding 等配置方式 |
| 历史设计与证据 | `review/`、`requirements/`、`design-input/` 等 | 某一时间点的设计输入与审查记录，不代表当前状态 |

文档与代码冲突时，以项目实现基线、当前系统全景、最新迁移和代码为准。带日期的 Review/Handoff 只证明对应提交和环境中的结果，不应被引用为持续有效的测试结论。

## 核心契约入口

- [API 规范](./model-design/api-spec/README.md)
- [数据模型](./model-design/data-models/README.md)
- [状态机](./model-design/state-machines/README.md)
- [Agent 节点](./model-design/agent-nodes/README.md)
- [前端 UI 规范](./model-design/ui-spec/README.md)
- [端到端运行链路](./model-design/end-to-end-runtime-flow.md)
- [Prompt 标准](./standards/prompts/README.md)
- [安全与合规](./standards/security-and-compliance.md)

## 项目边界

- 独立 FastAPI 单体 + React SPA，不基于 ClawAgent；
- 单核心 Agent + 受控节点，不使用多 Agent 框架；
- PostgreSQL + pgvector，不在 MVP 引入 Redis/Celery；
- Runtime 模型访问统一经过 Provider Protocol；
- Agent Run 事件持久化后才通过 SSE 推送；
- 默认 Mock 模式可离线验证，真实 Provider 必须显式启用；
- 当前为单机、单后端 Worker 部署，不宣称已经完成大规模生产验证。
