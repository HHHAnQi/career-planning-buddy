# Provider 配置与部署

## 单一配置契约

根目录 `.env` 是本地与 Docker Compose 的统一值来源，`.env.example` 是无密钥模板。后端 `Settings` 是字段、类型、默认值与组合约束的权威契约。不得新增 `COMPOSE_*` Provider 副本；Compose 直接透传 `LLM_*`、`JUDGE_LLM_*`、`SEARCH_*` 和 `EMBEDDING_*`。

从旧配置升级时执行 `cd backend && python -m scripts.migrate_legacy_env`。脚本只迁移键名：规范字段已有值时保留规范值，否则复制旧值，然后删除 `COMPOSE_*` 行；命令永不输出配置值。

配置加载链路：

```text
.env / deployment secrets
  → Docker environment or local process environment
  → Pydantic Settings validation
  → application-scoped Provider Registry
  → Agent executor and Tool Registry
```

测试设置 `APP_ENV=test` 后不会读取开发者 `.env`，避免 CI 或单测访问真实 Provider。真实 Provider 配置错误不会静默回退到 Mock。

## Provider 模式

| 能力 | Mock/Fixture | 真实模式 | 必填配置 |
|---|---|---|---|
| Planning LLM | `mock` | `openai_compatible` | `LLM_API_KEY/BASE_URL/MODEL` |
| Pairwise Judge | `mock/fixture` | `openai_compatible` | `JUDGE_LLM_API_KEY/BASE_URL/MODEL` |
| Search | `mock` | `baidu` | `BAIDU_SEARCH_API_KEY` |
| Embedding | `mock` | `local` | 容器内 `EMBEDDING_MODEL_PATH` |

`APP_ENV=production` 禁止 Planning LLM、Search 或 Embedding 使用 Mock。`EVAL_PROVIDER_MODE=live` 要求真实 Planning LLM。Judge 选择 `openai_compatible` 时必须使用完整的独立 Judge 配置。

## LLM Provider Profile 与任务模型

`LLM_PROVIDER` 控制 Mock/真实运行模式；真实模式下用 `LLM_PROVIDER_NAME` 声明供应商能力：
`openai`、`zhipu`、`deepseek`、`openai_compatible`。`auto` 仅用于兼容旧配置，部署环境推荐
显式填写，避免兼容网关被错误识别。

`LLM_MODEL` 是默认模型。以下可选字段只覆盖对应任务，留空时回退到默认模型：

- `LLM_PLANNING_MODEL`；
- `LLM_GOAL_UNDERSTANDING_MODEL`；
- `LLM_EVIDENCE_DISTILLATION_MODEL`。

三个任务分别通过对应的 `*_REASONING=off|auto` 控制推理。结构化抽取和修复默认 `off`，
以降低延迟和无效 reasoning token；Provider Profile 负责转换成 GLM/DeepSeek 支持的参数。
统一协议、能力与观测边界见[统一 LLM Provider 与调用观测设计](../architecture/llm-provider-and-telemetry.md)。

## Docker 启动

Mock 或远程 API Provider：

```bash
cp .env.example .env
docker compose up --build -d
```

本地 Embedding 模型需要显式只读挂载：

```env
EMBEDDING_PROVIDER=local
EMBEDDING_MODEL_HOST_PATH=/absolute/host/path/to/model
EMBEDDING_MODEL_NAME=your-model-name
EMBEDDING_DIM=1024
```

```bash
docker compose -f compose.yaml -f compose.embedding.yaml up --build -d
```

Compose 把宿主机目录固定挂载为 `/models/embedding`，后端不读取宿主机路径，也不会自动下载模型。生产部署应以只读 Volume 或模型镜像提供权重。

## 脱敏状态检查

在后端运行环境中执行：

```bash
cd backend
python -m scripts.provider_status
```

命令只返回 Provider 名称、是否配置完成、是否真实模式、缺失字段名和警告；不返回密钥、Base URL 或模型名。`ready=true` 表示配置契约完整，不代表外部 Provider 的网络可达性。

`/health/live` 只用于进程存活检查；`/health/ready` 检查数据库连通性、Alembic
版本和上述脱敏配置契约。两者都不调用计费 API。旧 `/health` 保留兼容性。
外部连通性应通过受限的 smoke test 验证，避免健康探针产生费用或触发限流。

## 完整性审查

每次增加、删除或重命名配置字段后运行：

```bash
cd backend
python -m scripts.audit_config
```

审查覆盖：

- 每个 `Settings` 字段都出现在 `.env.example`；
- 模板不存在未知字段或重复字段；
- 不允许遗留 `COMPOSE_*`；
- 不允许浏览器可见的 `VITE_*` 使用敏感字段名；
- `compose.yaml` 不引用旧 Provider 变量。
- 每个 `Settings` 字段都显式传入 Docker 后端，避免 `.env` 修改后容器仍使用代码默认值；
- Compose 引用的每个变量都存在于 `.env.example`，包括 Embedding 挂载覆盖文件。

## Secret 边界

- `.env` 仅用于本地开发并保持 Git ignored；
- 真实密钥不得写入 `.env.example`、Compose、前端构建参数、日志或 Trace；
- staging/production 应由部署系统注入 Secret；
- 脱敏诊断命令只读环境变量，不提供写入和持久化密钥的能力；
- 密钥轮换后重启后端进程，使缓存的 Settings 和 Provider 客户端重新创建。
