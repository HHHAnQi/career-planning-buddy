# Developer Eval API

Stage 5 实现。固定数据集文件位于 `backend/evals/datasets/*.jsonl`。

## GET /api/v1/dev/evals/datasets

返回可用 JSONL 数据集和 case 数量。

## POST /api/v1/dev/evals/experiments

```json
{
  "dataset": "career_plan_v1",
  "model_override": null,
  "prompt_version": null,
  "parallelism": 4
}
```

Response 202：experiment_id、status、poll_url。

当前实现入口为 `POST /api/v1/eval/runs`。确定性回放请求必须额外携带：

```json
{
  "provider_mode": "fixture",
  "run_type": "fixture_replay",
  "fixture_source_experiment_id": "completed-source-experiment-uuid"
}
```

服务端在创建阶段冻结逐 Trial 来源；缺少来源、数据集不一致、源实验未完成、
源 Trial 非 completed 或没有唯一 Fixture Bundle 时返回 409/422，不启动后台任务。

## GET /api/v1/dev/evals/experiments/{id}

返回：总 case、完成数、通过率、平均延迟、平均成本、各 grader 分数和失败 case。

第一版可将实验结果保存为 JSON Artifact；需要多人共享历史后再增加 Eval 表迁移。
