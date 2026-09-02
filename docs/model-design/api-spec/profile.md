# Profile API

## GET /api/v1/profile

返回当前用户画像。未建档返回 404 `NOT_FOUND_PROFILE`。

## PUT /api/v1/profile

首次创建或完整替换画像。需要 `Idempotency-Key`。

```json
{
  "goal_type": "agent_app",
  "stage": "preparing",
  "time_budget_minutes": 120,
  "skill_level": "intermediate",
  "skill_summary": "熟悉 FastAPI、RAG 和基础 Agent 开发",
  "start_date": "2026-09-01",
  "deadline": "2026-10-26",
  "preferences": {
    "target_companies": ["字节跳动"],
    "preferred_time_slot": "evening",
    "weekly_available_days": [1,2,3,4,5]
  }
}
```

必填：goal_type、stage、time_budget_minutes、skill_level、start_date、deadline。

## PATCH /api/v1/profile

部分更新，必须带当前 `version`：

```json
{
  "version": 2,
  "time_budget_minutes": 90,
  "skill_summary": "..."
}
```

成功响应返回完整 Profile 和新 version。

## 约束

- `time_budget_minutes`: 15~480；
- `start_date` 与 `deadline` 共同定义用户确认的闭区间；`start_date <= deadline`，区间最长 8 周；
- 首次计划从 `max(本地今日, start_date)` 开始，任何任务都不得早于开始日期或晚于结束日期；
- PATCH 不允许清空开始/结束日期；历史上缺少任一日期或结束日期已经过去的画像会被视为未完成，并重新进入资料补全页；历史值仍可读取用于表单预填；
- `preferences` 只允许已定义字段；
- version 冲突返回 409；
- 请求不允许 user_id。

Profile API 只保存画像，不静默替换现有计划。前端在用户明确选择“保存并重新规划”后，先完成 PATCH，再以当前 generated/active Plan、否则最近 completed Plan 为 `source_plan_id` 创建 replan Run；新 Run 会读取更新后的画像和当前执行事实。
