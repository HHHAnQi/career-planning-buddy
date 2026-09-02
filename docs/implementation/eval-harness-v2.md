# Eval Harness V2 实施记录

> **Historical implementation note.** 本文保留 PR-0～PR-2 的递进施工记录，不再代表
> 当前能力边界。当前状态见 [`../../README.md`](../../README.md) 与
> [`../architecture/current-system-overview.md`](../architecture/current-system-overview.md)。
> 当前代码已经实现 TrialRunner、ProviderCall audit、fixture replay、确定性 grader、
> Eval API、Pairwise Judge/Calibration，以及 live Eval 的有界 retry/backoff/pacing。

## 范围

V2 按实施计划递进交付。以下“后续”仅描述当时 PR-0/PR-1/PR-2 的历史边界，不能据此
判断当前缺口。线上漂移监控仍不在 v1 范围；统计门禁只有在足量真实人工 calibration
后才可从 `diagnostic_only` 升级。

## PR-0：Truth Baseline 与契约冻结

- 移除恒真的 `snapshot_replay` grader；
- 保留兼容 API，但将现有行为命名为 `legacy_trace_clone`；
- API 响应、Trace 和 config snapshot 显式记录 execution kind；
- 以 [`eval-harness-v2-baseline.md`](./eval-harness-v2-baseline.md) 保存改造前事实基线；
- 无数据库迁移，旧 Run 与旧 API 路径保持可读。

## PR-1：Evidence Integrity

- `EvidenceVisibility` 是候选级值对象，冻结逻辑调用 ID、完整目录 hash、可见引用和被截断引用；
- normal、force-final、format repair、business repair 都显式接收证据目录；
- candidate 只按产生它的 Provider 调用可见范围校验，不按 Run 全局目录校验；
- 空引用合法且不会被 Graph 自动补齐；
- 失败 Tool 或被 Tool 压缩器移除的项目不会进入可见目录；
- Trace 只保存 ID、hash、count，不保存证据正文；
- finalizer 保存候选原样引用，并在事务前再次验证可见性。

## PR-2：Case—Experiment—Trial—Grade 控制面

- `EvalCase`、Dataset Manifest、Experiment config 和 `GradeResult` 全部使用 strict Pydantic
  Contract，未知字段、缺失版本、Dataset/Fixture hash 篡改直接拒绝；
- `stage5-v1.jsonl` 保留原文件，通过 adapter 生成 30 条 V2 Case；Manifest 冻结源文件
  SHA-256 和 Case 数量，不把历史 Case 绑定到在线 API DTO；
- `eval_experiments` 冻结 Git、Graph、Prompt、Model、Tool、Context、Memory 与 Dataset
  版本，`execution_mode` 与 `variant_role` 独立建模；
- `eval_trials` 对 `(experiment_id, case_id, trial_index)` 建立唯一约束，每条 Case 创建
  pending Trial，但本 PR 不运行 Agent；
- `eval_scores` 原生支持 boolean、numeric、categorical 三种指标，不把类型强制压成 float；
- Service 和 PostgreSQL trigger 共同限制 Experiment/Trial 状态迁移，并禁止启动后的版本
  组合变更；未完成或没有真实 `run_id` 的 Trial 在 Service 与数据库两层均不能写 Grade；
- Alembic `20260805_0008` 已从空库 upgrade，并在隔离数据库完成 downgrade→upgrade 验证。

PR-2 只建立控制面和数据骨架，不宣称 Agent 已真实执行，不生成占位 Score，也不新增
Grader。ProviderCall、授权 EvidenceItem、TrialRunner 和真实 Runtime Outcome 仍由后续 PR 交付。

## 当时的后续顺序（现已大部分完成）

PR-3 真实 Runtime TrialRunner、Evidence Projection/确定性 Grader、Fixture Replay、
Fault Injection、多 Trial/Judge、可观测性与 CI smoke 均已在当前代码中落地。人工样本不足时
统计门禁仍保持诊断语义；不得把 Mock 规则回归指标描述为真实模型或生产质量结论。
