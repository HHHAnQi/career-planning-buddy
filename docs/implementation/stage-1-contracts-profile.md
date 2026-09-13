# Stage 1：契约、鉴权与用户画像

## 目标

把最基础的身份、Schema、数据库迁移和用户画像落地。

## 实现范围

1. 枚举：GoalType、CareerStage、SkillLevel、RunStatus、RunResultKind、PlanStatus、TaskStatus；
2. 表：users、user_profiles；
3. Guest 登录：`POST /api/v1/auth/guest`；
4. 当前用户摘要：`GET /api/v1/me`；
5. Profile：GET / PUT / PATCH；
6. JWT 依赖和用户数据隔离；
7. 乐观锁 version；
8. OpenAPI snapshot 与契约测试。

## 关键规则

- 请求体不允许携带 user_id；
- Guest 登录接收可选 device_id；相同 device_id 可复用用户；
- PUT 用于首次 upsert，PATCH 必须带 version；
- `time_budget_minutes` 范围 15~480；
- 画像未完成时 `/me` 返回 `profile_complete=false`，不是 500。

## 需要阅读

- `model-design/api-spec/auth.md`
- `model-design/api-spec/profile.md`
- `model-design/data-models/users.md`
- `model-design/data-models/user_profiles.md`

## 验收

- 新设备可取得 JWT；
- JWT 可读写自己的 Profile；
- 用户 A 无法读取用户 B；
- version 冲突返回 409；
- Alembic upgrade / downgrade 可执行；
- API、Service、Repository 测试通过。
