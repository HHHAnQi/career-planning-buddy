# Harness Workbench 需求澄清

状态：approved（设计已确认，代码未实现）。

## 目标

为 Agent Run 提供可定位问题的 Trace、可比较变更的 Replay 和可重复执行的 Eval，而不是建设独立的“大平台”。

## 已确认

- 运行时 Trace 使用 `agent_runs/agent_steps/tool_calls/agent_events`；
- Stage 2 先实现最小 Trace，Stage 5 再做开发者页面；
- Eval 第一版使用仓库内 JSONL，不要求先建 Eval 数据库表；
- Replay 使用保存的输入与 Tool fixture，允许更换 Prompt/模型做对比；
- `/api/v1/dev/*` 仅开发环境和 dev 角色可访问；
- 固定 Eval 数据集至少 30 条。

## 非目标

- 完全确定性复现真实网络搜索；
- 生产级实验平台；
- 多 Agent 调度、在线 A/B 或自动 Prompt 优化。
