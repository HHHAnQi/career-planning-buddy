# Python / FastAPI 编码规范

## 1. 基线

- Python 3.12；
- FastAPI、Pydantic v2、SQLAlchemy 2 Async；
- 全量类型标注；
- ruff、mypy、pytest 作为门禁；
- 异步路径不得调用阻塞网络或数据库函数。

## 2. 分层

```text
api → services → repositories
           └→ agent → providers/tools
           └→ harness
```

- Router 只做协议解析、身份依赖和响应映射；
- Service 负责用例、事务、幂等和状态机；
- Repository 负责查询，不返回 API DTO；
- Agent 节点不直接依赖 ORM Session；
- Provider 不包含业务状态转移。

## 3. Pydantic

- 请求、响应、内部状态分开建模；
- `model_config = ConfigDict(extra='forbid')` 为默认；
- 枚举继承 `str, Enum`；
- 服务端字段如 user_id、终态、计数字段不从客户端接收；
- 长度、范围和跨字段约束写 validator 并测试。

## 4. SQLAlchemy

- 使用显式事务；
- 用户资源查询必须带 user_id；
- 避免隐式 lazy load；
- 状态更新使用 version 乐观锁；
- N+1、缺索引和未限定分页在评审中阻断；
- 表结构只通过 Alembic 迁移修改。

## 5. 异步与外部调用

- LLM/Tool 调用统一设置 timeout；
- 使用 `asyncio.TaskGroup` 或明确取消传播，不创建无人管理的任务；
- 进程内 Task Registry 只作为本地执行句柄；可靠调度、取消和接管必须以数据库 lease 状态为准；
- 取消、超时和异常都要在 finally 中释放资源并持久终态。

## 6. 错误和日志

使用领域异常 code，不抛裸字符串；禁止 `except: pass`。日志采用结构化字段，不拼接 token、密钥或完整敏感内容。

## 7. 测试性

时间、UUID、Provider 和 Repository 通过依赖注入替换；核心规则写成纯函数；Mock Provider 输出确定，避免单元测试依赖真实网络。
