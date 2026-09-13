# Eval Harness 运行与可靠性边界

本文描述 Eval V2 在当前单体 MVP 中真实提供的并发、恢复和漂移语义。它是运行边界，
不是分布式任务平台承诺；基线仍禁止引入 Redis、Celery 和独立微服务。

## 1. 受控并发

- `EVAL_TRIAL_CONCURRENCY` 控制单个 Experiment 内同时执行的 Trial 数，范围 1～8，
  默认 1。结果按冻结 Trial 顺序汇总；任一 Trial 抛出未处理异常时，其余协程会被取消，
  Experiment 进入 `failed`。
- live provider 还受 `EVAL_LIVE_CONCURRENCY` 和 pacing/retry 参数约束。Trial 并发不是
  provider 并发的替代品，两层上限同时生效。
- 两个上限都是进程内上限。普通 Experiment 没有跨进程 lease；生产部署必须指定单一
  Eval worker。Pairwise Sweep 另有 PostgreSQL advisory lock，可避免多进程重复泵送同一 Sweep。

## 2. 恢复语义

| 工作类型 | 进程重启后的行为 | 不承诺的行为 |
| --- | --- | --- |
| Agent Run | 启动时按既有 executor 契约恢复或终结 | 跨区域 exactly-once |
| 普通 Eval Experiment | `running` Experiment 标记 `failed`；pending Trial 标记 `cancelled`，running Trial 标记 `failed`，统一记录 `PROCESS_INTERRUPTED` | 从半个 Trial 内继续执行 |
| Pairwise Sweep | 启动时重新提交 `running` Sweep；有结果的 running Item 对账为 completed，无结果的 Item 重新排队 | Redis/Celery 式分布式 lease |

普通 Eval 选择“可归因地失败”而不是猜测性续跑，因为 Runtime 可能已产生业务 Plan、Event
和 provider 成本。重新执行应创建新 Experiment，保留原失败证据。

## 3. Fixture 不可变性

- 普通 `evaluation` Trial 在 fixture mode 录制一个 bundle；`fixture_replay` Trial 必须显式
  指定已完成的来源 Trial，且两者 `case_fixture_hash` 必须一致。
- bundle 同时保存脱敏 response projection 和 lossless JSON replay payload。投影用于审计，
  payload 只用于重放；0020 之前的 projection-only bundle 会被拒绝，必须重新录制。
- 每次调用按 sequence、provider kind/method、retry attempt 和 request projection hash 校验；
  缺失、多余或漂移调用立即 `FixtureDesyncError`，不会回退到底层 provider。
- replay 不创建新 bundle；来源 Trial ID 和 bundle hash 写入目标 Trial 的冻结 outcome snapshot。

lossless payload 可能包含模型生成内容，数据库访问与保留策略应按运行数据处理，不能导出到
grader 的授权 EvidenceItem 或普通 API 响应。

## 4. 漂移边界

- 离线门禁冻结 dataset/source hash、case fixture hash、runtime identity、prompt/model/tool/context/
  memory/search/harness version和 fixture bundle。`--require-all-hard-gates` 要求所有 Trial 完成、
  全部已评分且硬门禁通过率为 1，否则返回非零。
- live Eval 只能声明“在该冻结配置和调用时间下的观测”，不能声明字节级可复现。模型服务端
  权重、路由、搜索索引和外部网页变化都属于外部漂移。
- 当前 MVP 没有持续漂移监控或自动回滚。发布门禁应比较显式 baseline/candidate Experiment；
  Pairwise calibration 在足量人工标注前保持 diagnostic-only。若需要长期线上监控，必须先修订
  project baseline，再引入调度、告警和保留策略。

## 5. 推荐运行参数

- CI/offline mock：`EVAL_TRIAL_CONCURRENCY=1`，使用严格硬门禁。
- fixture 重放：保持 `EVAL_TRIAL_CONCURRENCY=1` 便于定位 desync；批量确认稳定后可小幅提高。
- live 小流量：从 Trial 并发 1、provider 并发 1～2 开始，并按限流和数据库连接池观测调整。

