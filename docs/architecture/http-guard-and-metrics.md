# HTTP 限流与指标接入指南

本文说明后端的两个 HTTP 边界防护能力：请求限流（`RateLimitMiddleware`）与 Prometheus 指标（`GET /metrics`），以及开发者用量报表接口。对应实现位于 `backend/app/core/rate_limit.py`、`backend/app/core/metrics.py`、`backend/app/api/metrics.py`。

## 请求限流

### 行为

- **固定窗口计数**：以 60 秒为一个窗口，按身份键计数；超出预算的请求返回 `429 Too Many Requests`，并带 `Retry-After: 60` 响应头。
- **身份键**：`客户端 IP + Authorization 头哈希`。携带不同令牌的登录用户各自拥有独立额度（即使共享同一出口 IP）；未认证流量按 IP 共享一个桶。
- **豁免**：`/health`、`/health/live`、`/health/ready`、`/metrics`、`/docs`、`/openapi.json`，以及所有 `OPTIONS` 预检请求，永不限流。
- **内存回收**：过期窗口的计数在窗口切换时清扫，内存占用有界。进程内计数意味着重启后清零——与单 Worker 部署契约一致。

### 配置

| 变量 | 默认值 | 说明 |
|---|---|---|
| `RATE_LIMIT_PER_MINUTE` | `0`（关闭） | 每身份每分钟允许的请求数。`0` 完全禁用限流 |

- 测试环境默认关闭（`0`），避免测试套件触发限流。
- Compose 部署默认 `120`（见 `compose.yaml`），可通过根目录 `.env` 覆盖。

### 设计取舍（面试常见追问）

- **为什么不用 Redis + 令牌桶？** 当前部署是单机单 Worker，进程内固定窗口已足够；引入 Redis 会破坏"MVP 不新增外部依赖"的架构约束。多副本部署时再升级，接口契约（429 + Retry-After）不变。
- **为什么固定窗口而非滑动窗口？** 实现确定性高、可单测；窗口边界突刺（最多 2 倍瞬时流量）在单机演示场景可接受。

## Prometheus 指标

### 端点

`GET /metrics`，`text/plain; version=0.0.4`，无需认证（单机部署契约；公网暴露前需在反向代理层加认证）。端点不在 OpenAPI 契约中（`include_in_schema=False`）。

### 指标清单

| 指标 | 类型 | 标签 | 说明 |
|---|---|---|---|
| `http_requests_total` | Counter | `method`, `path`, `status` | 请求计数；`path` 已归一化（UUID 与数字段折叠为 `{uuid}`/`{id}`）防止标签爆炸 |
| `http_request_duration_seconds` | Summary | `method`, `path` | count/sum 近似（无分位数） |
| `http_requests_in_flight` | Gauge | — | 处理中的请求数 |
| `http_rate_limit_rejections_total` | Counter | `path` | 被限流拒绝的请求数 |

### 接入 Prometheus

在 `prometheus.yml` 中添加：

```yaml
scrape_configs:
  - job_name: career-planning-buddy
    scrape_interval: 15s
    metrics_path: /metrics
    static_configs:
      - targets: ["localhost:8000"]
```

注册表是进程内实现（`app/core/metrics.py`），重启清零。若迁移到 `prometheus-client`，调用点无需改动，只需替换模块内部实现。

## 开发者用量报表

`GET /api/v1/dev/usage-report?days=30`（需 dev 角色 JWT）。聚合窗口内所有 Run 与 Provider 调用：

- **totals**：Run 数（按 completed/degraded/failed 分列）、fallback 次数、总成本（CNY）、token 总量、单 Run 平均成本、延迟 P50/P95/max（最近邻秩法，仅统计终态 Run）。
- **graphs**：按 `(graph_version, model_id)` 切片的 Run 数、成本、平均延迟。
- **daily**：按 UTC 日聚合的 Run 数与成本，用于趋势图。
- **provider_kinds**：按 Provider 类型的调用数、错误数、按调用数加权平均延迟。

数据来源为 `agent_runs`（`total_cost_cny`/`total_latency_ms`/tokens）与 `provider_calls`（延迟、状态），无需额外埋点。

## 验证

```bash
# 限流（本地起服务后，连续请求直至 429）
for i in $(seq 1 130); do curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/v1/auth/guest -X POST; done

# 指标
curl -s http://localhost:8000/metrics | head -20
```

对应测试：`backend/tests/test_rate_limit.py`、`backend/tests/test_metrics_endpoint.py`、`backend/tests/test_dev_usage_report.py`。
