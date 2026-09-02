# Plans API

## GET /api/v1/plans/active

返回状态为 generated 或 active 的当前行动计划。无计划返回 404。

## GET /api/v1/plans

Query：`status`, `date_from`, `date_to`, `cursor`, `limit`。返回 Cursor 分页历史。

## GET /api/v1/plans/{plan_id}

返回 PlanDetail 视图：

```json
{
  "plan_id": "c91f8734-2839-4f55-9db1-1c39b8a410f2",
  "status": "active",
  "plan_date": "2026-07-31",
  "horizon_start": "2026-07-31",
  "horizon_end": "2026-09-03",
  "overall_direction": "五周内完成 Agent 项目闭环并进入模拟面试阶段",
  "weekly_focus": [
    {
      "week_index": 1,
      "focus": "补齐可演示的 Agent Run 闭环",
      "success_signal": "能够演示一次完整 Run 和 Trace"
    }
  ],
  "summary": "今天先把 Run Trace 的数据库闭环跑通",
  "rationale": "...",
  "adjustment_reason": null,
  "tasks": [],
  "sources": [
    {
      "kind": "search_source",
      "id": "f880d3e2-2de7-48aa-b123-068d1d6f5e69",
      "title": "示例来源",
      "url": "https://example.com",
      "reliability": 0.8
    }
  ],
  "companion_message": "...",
  "version": 2,
  "adopted_at": "...",
  "created_at": "..."
}
```

PlanDetail 由 plans、tasks、`weekly_focus_json`、`evidence_refs_json`、对应 Memory/ExperienceAtom/SearchSource 和 companion_messages 拼装。来源字段按 kind 返回适合用户展示的摘要；Memory 不返回不必要的敏感原文。

## GET /api/v1/plans/{plan_id}/sources

按 `evidence_refs_json` 顺序返回该计划实际使用的证据。支持 kind：memory、experience_atom、search_source。

- search_source 可返回审核后的 URL/title/snippet；
- experience_atom 返回 title/evidence/reliability；
- memory 返回类型和脱敏摘要，不暴露被关闭或删除的原文；
- 引用失效时返回 `available=false`，不 silently 替换为其他证据。

## 规则

- 用户只能读取自己的计划；
- 活跃计划定义为 generated/active；
- completed 计划仍可作为“生成下一固定周期”的来源；
- 新 continue/adjust 计划事务成功后，来源计划转 archived；
- 无单独“采纳”接口，首个任务开始即视为采纳；
- `weekly_focus` 只作为 1~8 周总览；当前 Plan 的 `tasks` 通常展开从 `plan_date` 开始的 7 天执行表，最后一个周期在目标日期不足 7 天时只展开剩余 1~7 天。
- `/today` 只展示当天 Task；周周期总览在 `/plans/{plan_id}` 对应的路线页展示。
- 每日总览展示 `starter_action`、`deliverable` 和 `rationale`；未来日期任务可预览，到 `scheduled_date` 后自动进入当天可执行区。
- 未来周保持方向级重点；当前周期不因每天或全部任务提前完成而补入新日期。只有固定周期到期后，才可经用户确认从原 `plan_date + 7 天` 开始下一周期；如果该日期已超过目标日期，则只做最终复盘，不再生成计划。
- 路线页的执行进度按当前固定周期的实际自然日计算：首日为 `1/7`，最后不足 7 天时以实际天数为分母，未来周期为 `0/N`，不能用“当前周序号/总周数”替代执行进度。
- Plan `version` 是乐观锁技术字段，不作为用户可见的路线版本名称。归档计划在路线页并入“每周重点”，以已结束的 7 天周期或实际短周期展示，不单独使用含义不清的“历史路线”。每个周期重点可进入详情，查看重点、成功信号和已展开的每日任务。
