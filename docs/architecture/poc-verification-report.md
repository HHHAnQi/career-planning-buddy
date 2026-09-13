# 运行时 Provider Smoke Checklist

本检查不阻塞 Stage 0~2。真实模型在 Stage 3 接入前验证；Tool Calling 在 Stage 4 前追加验证。

## 1. 目标

验证当前 OpenAI-compatible Provider 是否满足本项目的结构化生成和错误治理需求，而不是验证某个模型项目代号。

## 2. Stage 3 必测项

| 编号 | 验证 | 通过标准 |
|---|---|---|
| H1 | 基础结构化调用 | 能按 Pydantic Schema 返回结果 |
| H2 | JSON Schema 稳定性 | 20 次固定输入 Schema 成功率达到项目阈值 |
| H3 | 非法输出处理 | Provider 能返回统一 StructuredOutputError，Runtime 可修复一次 |
| H4 | 超时和限流 | 映射为统一 Provider 异常，不泄漏密钥/原响应 |
| H5 | Token Usage | 能读取或可靠估算输入/输出 Token |
| H6 | 实际 model id | 返回可追踪 model/provider/request id |
| H7 | 中文计划质量 | 固定 Case 通过时间预算、可启动性、可验证性规则 |
| H8 | 取消/Deadline | 超时后不会继续下一节点或持久化伪成功 |

项目 SSE 来自持久化 `agent_events`，不要求模型 Provider 支持 token streaming。

## 3. Stage 4 Tool Calling 追加项

| 编号 | 验证 | 通过标准 |
|---|---|---|
| T1 | Tool Call | 返回白名单 Tool 名与合法参数 |
| T2 | Final/Tool 互斥 | 不同时返回 Tool Call 和 Final Result |
| T3 | 两轮收敛 | 在 2 轮/4 次预算内返回最终 PlanCandidate |
| T4 | 未知 Tool | Adapter/Runtime 明确拒绝 |
| T5 | Tool 结果回填 | evidence 不被当作系统指令 |

如果具体模型不支持原生 Tool Calling，可由 Provider Adapter 使用统一 JSON AgentTurn Schema，但上层契约不变。

## 4. 配置

```env
LLM_BASE_URL=...
LLM_API_KEY=...
LLM_MODEL=...
LLM_ROUTER_MODEL=...
```

Codex 只用于开发，不出现在运行时配置中。

## 5. 结论记录模板

```text
日期：
Provider：
实际 Model ID：
协议/Adapter：
Stage 3 H1-H8：
Stage 4 T1-T5（如适用）：
平均 Token/延迟/成本：
已知限制：
结论：Go / Conditional Go / No-Go
```

失败时先确认 Provider Adapter、Prompt、Schema 和参数；需要更换模型时只调整配置/Provider 实现，不修改业务层 DTO 和状态机。
