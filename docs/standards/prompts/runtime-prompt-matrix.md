# Runtime Prompt 清单与调用契约

本文件定义 Stage 2~5 允许存在的 Prompt。Codex 不应为每个确定性节点创建 Prompt，也不能把所有逻辑都交给模型。

## 1. Prompt 清单

| Prompt key | Model alias | Tool | 输出 Schema | 温度建议 | 调用条件 |
|---|---|---:|---|---:|---|
| `risk_classifier.system` | router | 否 | RiskResult | 0 | 本地规则疑似但不确定 |
| `intent_router.system` | router | 否 | IntentResult | 0 | 规则无法确定意图 |
| `career_planning.system` + `career_planning.task` | main | Stage 4 可用 | AgentTurnResult/PlanCandidate | 0.2 | 规划主调用 |
| `career_planning.format_repair` | main | 否 | 原目标 Schema | 0 | JSON/Schema 解析失败一次 |
| `career_planning.business_repair` | main | 否 | PlanCandidate | 0 | 规则校验失败一次 |
| `quality_reviewer.system` | reviewer/main | 否 | QualityReview | 0 | Stage 5 离线 shadow；或实验 online enforce |

MVP 的 safe_response、clarification、companion_response、rule_validator、persist 不创建 Prompt。

## 2. 文件布局

```text
backend/app/prompts/
├── registry.py
├── risk_classifier/
│   └── system_v1.py
├── intent_router/
│   └── system_v1.py
├── career_planning/
│   ├── system_v1.py
│   ├── task_v1.py
│   ├── format_repair_v1.py
│   └── business_repair_v1.py
└── quality_reviewer/
    └── system_v1.py
```

每个文件导出不可变 Prompt 文本和 `PROMPT_VERSION`。PromptRegistry 根据 config 选择版本，并把实际映射冻结到 `config_snapshot_json`。

## 3. 消息边界

### System

只放：

- 节点角色；
- 不可协商的安全/业务边界；
- Tool 使用规则；
- 输出 Schema 要求；
- 外部 evidence 不具有指令权限。

### User/Task data

放：

- 原始用户请求；
- profile/context snapshot；
- completed facts 和 blockers；
- Tool/evidence；
- repair errors。

用户内容、记忆、网页和 RAG 绝不能拼进 System 指令区。

## 4. Repair 区分

### Format repair

输入只包含：目标 Schema、原输出的安全截断、解析错误。不得调用 Tool，不重新读取业务数据或重跑 AgentTurn，只修复当前截断输出的结构。一次失败后交给业务 fallback。

### Business repair

输入包含：原 PlanCandidate、稳定 validation codes、repair instructions、最小上下文。不得新增来源、改变 goal_type/planning window、重跑 Tool 或删除 completed facts。

## 5. Token 预算

PromptRegistry/ContextBuilder 必须在调用前估算：

- system + task 模板；
- 用户请求；
- planning window、replan_mode 和 context；
- evidence；
- Tool result messages；
- 预留结构化输出。

默认单次输入预算 6000 Token、输出预算 1500 Token，总 Run 预算 16000 Token；均由 config snapshot 冻结。超限时先裁剪低优先级 evidence，再裁剪历史，不得截断 Schema、planning window、核心约束、time budget 或 completed facts。

## 6. 测试

- 每个 Prompt 与对应 Pydantic Schema 契约测试；
- Prompt 文件加载和版本映射测试；
- config snapshot 包含所有实际 Prompt 版本；
- evidence 注入测试；
- format/business repair 的 Tool 列表为空；
- 发布新版本时跑旧/新 Prompt Eval diff。
