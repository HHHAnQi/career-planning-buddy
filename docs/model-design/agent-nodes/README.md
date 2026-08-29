# Agent 节点施工索引

项目共 10 个核心节点和 3 个增强能力。只有 `career_planning_agent` 是真正具备自主 Tool Calling 的 Agent；其余都是确定性节点、分类节点、模板节点或 Service 适配节点。

完整运行时、预算、快照、事件和终态规则见 [`../agent-runtime/README.md`](../agent-runtime/README.md)。

## 核心节点

| 节点 | 类型 | 主要副作用 | Stage |
|---|---|---|---:|
| [risk_gate](./risk_gate.spec.md) | 程序规则 + 可选分类模型 | Trace/Event | 2 |
| [safe_response](./safe_response.spec.md) | 固定安全响应 | 返回终态候选 | 2 |
| [intent_router](./intent_router.spec.md) | 版本化规则 + 歧义澄清；预留结构化分类扩展 | Trace/Event | 2 |
| [clarification](./clarification.spec.md) | 程序节点 | 返回终态候选 | 2 |
| [context_builder](./context_builder.spec.md) | 只读程序节点 | input snapshot | 2 |
| [career_planning_agent](./career_planning_agent.spec.md) | 唯一真 Agent | 模型/Tool Trace | 2/3/4 |
| [rule_validator](./rule_validator.spec.md) | 确定性规则 | Validation Trace | 2 |
| [revise_or_fallback](./revise_or_fallback.spec.md) | 一次受控修复或模板降级 | 模型 Trace | 2/3 |
| [companion_response](./companion_response.spec.md) | 模板节点 | 无业务写入 | 2/3 |
| [persist](./persist.spec.md) | terminal-aware Finalizer 适配节点 | Plan/Task/Run/Event | 2 |

## 增强能力

| 能力 | 类型 | 是否在主 Graph 阻塞 | Stage |
|---|---|---:|---:|
| [quality_reviewer](./quality_reviewer.spec.md) | LLM Judge；默认离线 shadow | 否，独立 Eval 记录 | 5 |
| [distill_evidence](./distill_evidence.spec.md) | `SearchSource → ExperienceAtomCandidate`，成功 Run 后 best-effort 执行 | 否；Agent Executor 终态后调用 | 6B |
| [memory_candidate_distiller](./memory_candidate_distiller.spec.md) | `Review → MemoryCandidate` 的确定性个人记忆候选提炼 | 否；Review Service 同事务调用 | 6A |

两者均已实现，但边界不可混用：`memory_candidate_distiller` 只产生用户私有、必须经
用户确认才可进入模型的 L2 候选；`distill_evidence` 只从当前 Run 的真实
`SearchSource` 产生 L3 候选，随后仍须开发者审核才成为可检索的全局
`ExperienceAtom`。L2 内容不得写入 L3。

## 每个节点 spec 必须回答

1. 输入和输出 DTO；
2. 前置条件与后置条件；
3. 是否调用模型、Tool、Repository 或 Service；
4. 允许的副作用；
5. 节点超时和预算；
6. 事件与 Trace 字段；
7. 可预期失败如何路由；
8. 至少一个 happy path 和一个失败测试。

所有节点输入输出必须可序列化，不允许把 ORM Session、SQLAlchemy Model、厂商 Client、文件句柄或协程对象放进 Graph State。
