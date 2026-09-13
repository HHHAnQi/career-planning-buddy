# AI 渐进式阅读指南

编码助手不需要一次加载全部文档。先读基线，再按任务补充上下文。

## 所有任务必读

- [根 AI 规则](../../AGENTS.zh-CN.md)
- [项目实现基线](../implementation/project-baseline.md)
- [当前阶段任务书](../implementation/README.md)

## 按任务加载

| 任务 | 追加阅读 |
|---|---|
| 产品或范围 | `overview/product-overview.md`, `overview/srs.md` |
| 全局架构 | `architecture/adr.md`, `architecture/tdd.md` |
| API | `architecture/api-and-data-contracts.md`, 对应 `model-design/api-spec/` |
| 数据表 | 对应 `model-design/data-models/` 与状态机 |
| Agent Runtime/Graph | `model-design/agent-runtime/README.md`、`model-design/tools/README.md`、端到端流程与对应 node spec |
| Prompt/LLM | `standards/prompts/`, `third-party-integration/llm-provider.md` |
| 安全 | `standards/security-and-compliance.md`, `agent-nodes/risk_gate.spec.md` |
| 测试/Eval | `standards/testing-and-tdd.md`, `model-design/harness/` |
| 任务流程 | `governance/development-workflow.md`, `requirements/<feature>/` |

`design-input/` 是历史归档，只在追溯早期决策时读取，不能用其中旧技术选型覆盖当前基线。

## 输出要求

编码助手完成一轮任务后必须说明：修改文件、关键决策、迁移影响、执行过的命令、真实结果和未解决问题。
