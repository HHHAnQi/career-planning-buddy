# Prompt 版本化规范

## 1. 文件命名

格式：`backend/app/prompts/{node_or_domain}/<purpose>_v<n>.py`

| 例子 | 含义 |
|---|---|
| `prompts/intent_router/system_v1.py` | intent_router 初始版本 |
| `prompts/intent_router/system_v2.py` | 经过评测的新版本 |
| `prompts/career_planning/business_repair_v1.py` | 业务修复 Prompt |

版本号只表示不可变修订顺序，不表示灰度/生产状态。是否默认启用由 PromptRegistry 配置决定。

## 2. 不可变规则

Prompt 一旦被任何已保存 Run 的 `config_snapshot_json` 引用，就不得原地修改。后续改动新建 `_v2.py`、`_v3.py`。

尚未提交、未产生任何持久 Run 的开发版本可修改，但合并前应冻结。

## 3. Registry

`backend/app/prompts/registry.py` 维护 prompt key 到 version 的映射：

```python
DEFAULT_PROMPT_VERSIONS = {
    "risk_classifier.system": "v1",
    "intent_router.system": "v1",
    "career_planning.system": "v1",
    "career_planning.task": "v1",
    "career_planning.format_repair": "v1",
    "career_planning.business_repair": "v1",
    "quality_reviewer.system": "v1",
}
```

Run 创建时将实际映射冻结到 `config_snapshot_json`。agent_steps 记录调用点的 prompt key/version。

## 4. Replay

- 默认 Replay 使用原 config snapshot 的版本；
- 对比实验可显式覆盖一个或多个 prompt key；
- 不得因默认版本已升级而让历史 Replay 悄悄使用新 Prompt；
- 原版本文件缺失时 Replay 失败并报告 `PROMPT_VERSION_NOT_FOUND`。

## 5. 版本发布

Prompt 变更 PR 至少提供：

- 变更目标；
- 旧/新版本 Eval 通过率；
- 失败 Case 差异；
- Token/成本变化；
- 是否改变 Tool 使用或输出 Schema。

默认版本切换必须修改 Registry 配置并跑 Eval。不得自动根据一次线上失败篡改 Prompt。

## 6. 引用

- [Runtime Prompt 清单](./runtime-prompt-matrix.md)
- [Prompt 格式规范](./prompt-format-standard.md)
- [Agent Runtime](../../model-design/agent-runtime/README.md)
- [Trace 表](../../model-design/data-models/trace-tables.md)
