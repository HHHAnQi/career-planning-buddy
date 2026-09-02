# Auth API

## POST /api/v1/auth/guest

创建或复用 Guest 用户并签发 JWT。

### Request

```json
{
  "device_id": "browser-generated-random-id"
}
```

- `device_id` 可选，长度 16~128；
- 服务端只保存 SHA-256 hash；
- 缺失时创建一次性 Guest 用户。

### Response 200/201

```json
{
  "access_token": "...",
  "token_type": "bearer",
  "expires_in": 86400,
  "user": {
    "id": "3f42b5fa-16b8-45d4-a095-3c2d5dc1a35b",
    "display_name": null,
    "role": "user"
  }
}
```

相同有效 device_id 再次调用返回 200 并复用用户；首次创建返回 201。

## GET /api/v1/me

返回首页恢复所需摘要：

```json
{
  "user": {"id":"...","display_name":null,"role":"user"},
  "profile_complete": true,
  "profile": {},
  "active_plan": null,
  "today_tasks": [],
  "latest_review": null,
  "active_run": null
}
```

该端点只做聚合查询，不生成计划、不写业务状态。

## 安全

- JWT 使用 HS256 或更高强度算法，secret 仅来自环境变量；
- 不记录原始 device_id；
- MVP 无密码和 OAuth，不得伪造“OAuth token”流程；
- `/dev/*` 额外校验 role=dev。
