# 产品导航与页面状态

## 主导航

Today / Plans / Reviews / Memories；开发模式额外显示 Dev Trace。

## 首次进入

```text
无 token → /login 自动 guest 登录
有 token + profile_complete=false → /onboarding
有 token + profile_complete=true → /today
```

## Today 页面

- active Run 卡片：连接 SSE；
- 今日任务；
- 无计划时显示“生成计划”；
- SSE 只显示临时进度，终态后重新 GET Run；
- 根据 result_kind 渲染：
  - plan：invalidate `/plans/active` 与 `/tasks`；
  - clarification：显示固定问题并跳转/内嵌 Profile 表单；
  - navigation：显示用户可点击的目标页面动作；
  - safe_response：显示审核后的安全响应，不显示计划卡片；
  - failed/cancelled：显示重试或返回，不伪装有结果；
- SSE 断线显示重连，不清空已有权威数据。

## 前端状态来源

TanStack Query 管理 API 数据；SSE 事件只更新临时进度并触发 Query invalidate。不要把完整业务事实只放在 Zustand 或组件内存。

刷新恢复顺序：GET Run → 根据 result_kind 拉 Plan/Task 或渲染 terminal payload。
