# Model Design 施工入口

本目录把架构拆成可直接实现的纵切 spec。

| 子目录/文档 | 作用 |
|---|---|
| [API Spec](./api-spec/README.md) | Router、Request、Response、错误 |
| [Data Models](./data-models/README.md) | SQLAlchemy 与 Alembic 字段事实源 |
| [State Machines](./state-machines/README.md) | Run/Plan/Task/意图状态 |
| [Agent Runtime](./agent-runtime/README.md) | Graph、State、预算、快照、终态与失败收敛 |
| [Agent Nodes](./agent-nodes/README.md) | 10 核心 + 2 增强能力 |
| [Agent Tools](./tools/README.md) | Tool 注册、白名单、契约、预算与 Replay fixture |
| [Feature Flows](./feature-flows/README.md) | 用户用例纵切 |
| [Harness](./harness/README.md) | Trace、Event、Replay、Eval |
| [UI Spec](./ui-spec/README.md) | 页面和状态映射 |
| [端到端运行流程](./end-to-end-runtime-flow.md) | 全链路总览 |
| [数据与知识来源](./data-seeding-and-sources.md) | Search、RAG、经验原子 |

权威优先级：implementation baseline > architecture > 本目录。`design-input/` 不参与实现决策。
