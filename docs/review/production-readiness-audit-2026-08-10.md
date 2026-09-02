# 生产就绪审查与演进边界（2026-08-10）

## 结论

当前 Agent Run 已从单 Worker 基线升级为 PostgreSQL 租约调度，可由多个后端 Worker 竞争接管；
但 Eval/Pairwise、部署观测和节点级 checkpoint 尚未达到完整多实例企业生产系统要求。
本轮只修复不改变产品拓扑、能被自动验证的问题；需要新基础设施或真实凭据的能力明确延期，
避免为了架构观感引入未被需求验证的组件。

## 已确认并完成

| 问题 | 证据 | 本轮方案 | 验收 |
|---|---|---|---|
| `/health` 不能判断是否可接流量 | 只返回静态 `ok` | 拆分 live/ready；ready 检查 DB、迁移和配置 | API 测试 + Compose healthcheck |
| Provider 存在重复构建 | Memory HTTP 依赖单独缓存 Embedding | 应用级 Registry 一次构建并共享 | 对象身份测试 |
| 意图规则无独立回归门禁 | 规则只被运行链路间接覆盖 | 版本化中英 JSONL + 确定性评测命令 | CI 要求 20+ 用例 100% |
| 后端容器默认 root | Dockerfile 未切换用户 | 安装后切换专用 `app` 用户 | 镜像配置检查 |
| 配置与基线文档漂移 | 基线保留已删除字段 | `.env.example` + `Settings` 作为唯一事实源 | 配置审计进入 CI |
| Memory 决策未消费 Idempotency-Key | 服务直接丢弃 header | 持久化 key/hash/action 并检测冲突 | API 重放与复用冲突测试 |
| Agent Run 仅靠进程内调度 | 重启将活动 Run 直接失败 | PostgreSQL claim/lease/heartbeat + bounded requeue + attempt fencing | lease、接管、重试耗尽、旧 attempt 拒写测试 |

意图识别继续采用“确定性规则优先”的实现，原因是当前可执行意图仅创建、继续、调整和澄清，
边界小且关系到状态机写操作。LLM 不应成为所有请求的必经分类器。未来只有在真实语料显示长尾
召回不足时，才在低置信度分支增加结构化 LLM fallback，并保留规则覆盖、超时降级和离线评测。

## 确认存在但延期

| 能力缺口 | 为什么本轮不直接实现 | 上线前置条件 |
|---|---|---|
| 节点级 durable checkpoint | Agent Run 已有数据库 lease，但重试仍从 Graph 起点开始 | 冻结节点输入/输出；验证 LLM 重放和副作用边界 |
| Eval/Pairwise 多 Worker | 这些 executor 仍是进程内任务 | 复用 lease 协议或采用成熟队列；完成接管测试 |
| 真实 Search | 需要供应商凭据、配额与内容质量基线 | 凭据注入；超时/限流/熔断；来源合规；检索数据集验收 |
| 真实 Embedding | 需要模型制品、维度锁定和资源容量 | 模型版本与哈希；只读制品；召回评测；滚动升级方案 |
| 集中式 Secret | Compose `.env` 只适合本地 | 由目标部署平台选择 Secret Manager；完成轮换和最小权限演练 |
| OpenTelemetry/指标告警 | 当前只有结构化日志与业务 Trace | 先确定 SLO，再接 collector、指标后端和告警责任人 |
| API 限流与滥用防护 | 单机本地基线没有共享计数器 | 明确网关/身份边界；按用户与 Provider 配额设计 |
| 备份恢复 | 代码仓无法证明云数据库恢复能力 | 选定部署环境；自动备份；执行并记录恢复演练 |
| 浏览器端 E2E | 当前前端只有组件测试 | 引入 Playwright；固定测试数据与无外部计费的完整路径 |

## 后续决策门槛

1. 先从线上 Trace 构建匿名化意图语料，统计规则的 unsupported、误路由和澄清率。
2. 若规则在目标语料达不到约定指标，再对 Embedding 分类器与结构化 LLM fallback 做同集对比。
3. 只有准确率提升能覆盖成本、延迟和不可用风险时才启用混合路由；高风险与强业务规则仍由规则兜底。
4. 从单 Worker 升级前，必须先完成调度 ADR 和故障注入测试，不能只增加进程数。
