# requirements/ 目录入口

本目录保存一次具体改动的澄清、计划和任务，不作为正式契约的替代品。

## 何时创建

新增 API、表、Agent 节点、状态机，或跨多个模块的功能时，创建：

```text
docs/requirements/<feature>/
├── clarify.md
├── plan.md
└── tasks.md
```

正式字段和状态仍需回写到 `architecture/` 或 `model-design/`。

## 推荐 feature

- `engineering-baseline`
- `profile-onboarding`
- `plan-generation`
- `today-task-execution`
- `review-and-replan`
- `memory-and-rag`
- `safety-gate`
- `harness-workbench`

## 任务字段

每个任务至少写：交付物、依赖、对应 API/表/节点、验收命令、是否可并行、完成状态。单任务尽量控制在 1~3 天。

阶段编号统一引用 [阶段交付定义](../governance/stage-delivery-definition.md)。
