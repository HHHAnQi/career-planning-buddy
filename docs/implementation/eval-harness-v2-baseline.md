# Eval Harness V2 改造前基线

基线提交：`41696dd04082f75d69565f99d81ab0440a91b042`（2026-08-04）。

| 项目 | 改造前事实 | 可信度 |
|---|---|---|
| 固定数据集 | `stage5-v1.jsonl`，30 case | 可复查 |
| Provider | `MockPlanningProvider` | 仅确定性回归 |
| 报告 | 12 个 grader，预期 30/30 | 不能外推真实模型质量 |
| `snapshot_replay` | 每个 case 硬编码 `True` | 无效指标，PR-0 删除 |
| `quality_reviewer` | 检查摘要、理由和任务非空 | 规则检查，不是 LLM Judge |
| `/dev/runs/{id}/replay` | 复制 Run、Step、Tool、结果和事件 | Trace Clone，不是重执行 |
| Replay diff | 未实现 | 不可评估 |
| 统计置信区间/多次采样 | 未实现 | 不可评估 |

本地宿主仅有 Python 3.9.6，而项目要求 Python 3.12；因此宿主不能作为有效验收环境。
PR-0/PR-1 完成后的命令与容器化验证结果记录在交付说明中，不能倒填为改造前能力。
