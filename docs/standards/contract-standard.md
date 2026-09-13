# 契约规范

## 1. 单一事实源

- HTTP 契约：`docs/model-design/api-spec/`
- 数据表：`docs/model-design/data-models/`
- 状态机：`docs/model-design/state-machines/`
- Agent 节点：`docs/model-design/agent-nodes/`

Pydantic DTO、OpenAPI、前端类型和测试必须从同一字段定义演进。

## 2. Pydantic v2

- 请求和响应使用不同模型；
- 枚举使用字符串值；
- 时间统一 ISO 8601 UTC；
- ID 为 UUID 字符串；
- 禁止接受客户端提交 `user_id`、Run 终态或服务端计算字段；
- 默认 `extra='forbid'`，除非兼容性另有说明。

## 3. API 规则

统一前缀 `/api/v1`。成功响应返回资源或明确结果；错误响应采用：

```json
{
  "error": {
    "code": "TASK_VERSION_CONFLICT",
    "message": "task has been updated",
    "request_id": "...",
    "details": {}
  }
}
```

分页使用 cursor；写操作通过幂等键或版本号避免重复提交。

## 4. 兼容性

删除字段、改变含义或缩窄枚举属于破坏性变更。先更新 spec 和契约测试，再改实现。OpenAPI snapshot 在 Stage 1 建立，后续 Pull Request 必须解释差异。

## 5. SSE 契约

事件必须有：`id(sequence)`、`event`、`data`。先持久化 `agent_events`，再发送。重连携带 Last-Event-ID，服务端按 sequence 补发，终态后发送 `run.completed`、`run.degraded`、`run.failed` 或 `run.cancelled`。
