# users — 用户

| 字段 | 类型 | NULL | 默认/约束 | 说明 |
|---|---|---:|---|---|
| id | uuid | NO | PK, gen_random_uuid() | 用户 ID |
| auth_type | varchar(16) | NO | CHECK guest/email/github, default guest | 登录类型 |
| guest_device_hash | varchar(64) | YES | UNIQUE WHERE NOT NULL | Guest 设备标识 hash，不存原 device_id |
| email | varchar(255) | YES | UNIQUE WHERE NOT NULL | 后续登录扩展 |
| display_name | varchar(64) | YES | | 显示名 |
| role | varchar(16) | NO | CHECK user/dev, default user | 开发者接口权限 |
| is_active | boolean | NO | default true | 禁用标记 |
| created_at | timestamptz | NO | now() | |
| updated_at | timestamptz | NO | now() | |

索引：`guest_device_hash` partial unique、`email` partial unique。

合法示例 UUID：`3f42b5fa-16b8-45d4-a095-3c2d5dc1a35b`。
