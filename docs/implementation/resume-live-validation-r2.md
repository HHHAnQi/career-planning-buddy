# Resume Agent R2 验证记录

## 已验证

- Alembic `20260901_0035 -> 20260902_0036` 在 PostgreSQL 16 + pgvector 环境升级成功；
- 后端完整测试套件通过（714 cases：应用/API 295，Eval V2 既有回归 419）；
- R2 定向测试覆盖真实 Runtime、Tool 消费、混合检索、来源 span、注入隔离、检查点、Exact
  Replay、Candidate Comparison 和语义 Diff；
- Ruff 与 Mypy 对 `app`、`evals` 通过；
- 前端 36 项测试与生产构建通过，覆盖材料页 Run 结果绑定、批量预览/应用行为；
- `resume-agent-v1` 的 10 条确定性诊断用例通过。

## 明确未验证 / 未实现

- **Live Provider 验收未执行**：当前环境没有用于本次验收的外部模型凭证、配额和审批，因此
  `resume_live_provider` 只能标记为代码路径已实现，不能标记 `verified_live`；
- **Resume Eval V2 未实现**：现有 10 条套件没有进入持久化 `Experiment -> Trial -> Score`
  管线，也没有人工校准集、阈值和 CI promotion gate，因此不得把 100% 诊断通过率解释为生产质量；
- **跨部署历史 Candidate Runtime 解析未实现**：Candidate Comparison 仅允许当前服务端生效的
  Runtime Bundle。任意历史模型/Prompt 的重建需要可寻址的 Provider/Prompt artifact registry。

## 验收口径

`implemented` 表示代码路径存在；`verified_mock` / `verified_fixture` 表示相应受控环境已经验证；
只有具备真实凭证、留存 Run/Trace 和人工抽检结果后才允许升级为 `verified_live`。
