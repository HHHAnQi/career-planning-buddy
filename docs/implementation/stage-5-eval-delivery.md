# Stage 5：Trace、Replay、Eval 与工程交付

## 目标

把项目做成可演示、可评测、可解释、可复现的秋招作品。

## 实现范围

1. 开发者 Run 列表与详情；
2. Trace 树：节点、模型、Tool、Token、耗时、错误、snapshot/version；
3. 30 条 JSONL 固定 Eval Case；
4. 规则 Grader：意图、任务数量、时间预算、可启动性、可验证性、来源、连续性、安全路由；
5. Bad Case 回流；
6. Replay V2：原输入快照 + 配置快照 + Tool fixture 的真实重执行与 diff；
7. quality_reviewer 默认由 Eval/Replay 离线 shadow；
8. Docker Compose 一键启动；
9. CI：lint、type check、test、migration、eval smoke；
10. README 截图、Demo、真实测试结果。

## Replay 范围

当前实现仅有 `legacy_trace_clone`，保留 `/dev/runs/{id}/replay` 路径用于兼容，但响应必须
显式标记执行种类，文档、报告和 Demo 不得把它作为 Replay 能力。以下内容是 V2 验收目标，
不是当前已交付事实。

MVP Replay 支持：

- 使用原 `input_snapshot_json`；
- 默认使用原 `config_snapshot_json`，或显式覆盖 prompt_version/model；
- Tool 使用 `tool_calls.result_json` fixture；
- 缺 fixture 时默认失败，不静默访问真实网络；
- 对比输出、规则指标、成本和延迟。

不承诺对 live 网络搜索完全确定性重放。

## quality_reviewer

- 默认由 Eval/Replay 在 Run 终态后离线 shadow，结果写独立 Eval 记录；
- 不向原 Run 追加 step/event，不作为唯一 Eval 真值；
- reviewer 失败不改变线上结果；
- 若实验性开启 online enforce，必须在 persist 前执行、计入 Run 预算，且修复后不再次调用 reviewer。

## 验收

- 30 case 可一条命令运行；
- 报告包含通过率、各 Grader、平均 Token、成本、延迟和失败 Case；
- 输入画像修改后 Replay 仍使用原快照；
- Tool fixture 缺失有明确错误；
- terminal event 唯一、结果 kind、fallback 原因可在开发者页看到；
- Docker Compose 从空环境可启动；
- 3~5 分钟 Demo 可完整走通建档、规划、任务、复盘、重规划和 Trace/Eval。
