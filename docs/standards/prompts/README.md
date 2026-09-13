# Prompt 规范

Prompt 是 Agent 项目的版本化工程工件，但只有确实需要模型判断或生成的节点才应拥有 Prompt。

## 文件清单

| 文件 | 覆盖 |
|---|---|
| [runtime-prompt-matrix.md](./runtime-prompt-matrix.md) | 允许的 Prompt、模型别名、Schema 和调用条件 |
| [prompt-format-standard.md](./prompt-format-standard.md) | System/User/Evidence 分层与结构化输出 |
| [prompt-versioning-standard.md](./prompt-versioning-standard.md) | 不可变版本、Registry、Snapshot 和 Replay |
| [prompt-review-checklist.md](./prompt-review-checklist.md) | Prompt PR 评审检查表 |

代码位置：`backend/app/prompts/{node_or_domain}/<purpose>_v<n>.py`。
