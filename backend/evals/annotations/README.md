# Rubric 标注操作指南

本目录是规划质量的 **golden set**：人工标注文件是校准 LLM Judge 的唯一基准，**纳入版本库**。

## 文件

| 文件 | 作用 | 生成方式 |
|---|---|---|
| `rubric-v1-worksheet.jsonl` | 待标注工作表（23 行：请求 + 画像 + 规划候选 + 空 `annotations`） | `LLM_PROVIDER=mock python -m scripts.build_rubric_annotation_set`（真实模型输出：配置真实 provider 后加 `--overwrite` 重新生成） |
| `rubric-v1-judge-scores.jsonl` | Judge 打分结果 | `python -m scripts.run_rubric_judge --judge deterministic`（四维全打分用 `--judge llm`，需 `JUDGE_LLM_*` 配置） |

## 标注步骤（预计 2–3 小时）

1. **先读评分标准**：[`docs/standards/plan-quality-rubric.md`](../../docs/standards/plan-quality-rubric.md)——四个维度的 1/3/5 分判据与标注协议（单专家、盲评、书面理由、反注水条款）。
2. **逐行标注**：编辑 `rubric-v1-worksheet.jsonl`，把每行的 `"annotations": null` 替换为：

```json
"annotations": {
  "goal_alignment": 4,
  "evidence_grounding": 3,
  "executability": 5,
  "horizon_compliance": 4,
  "rationale": "任务贴合画像但周三任务与当周focus脱节；交付物可检验",
  "annotator": "你的名字或ID",
  "annotated_at": "2026-09-05"
}
```

3. **前 3 条标完自查**：对照 rubric 判据回读一遍，确认没有"手松/手紧"。
4. **跑校准**：

```bash
python -m scripts.calibrate_rubric_judge
```

输出每维度的 Cohen's kappa（分带）/ Spearman ρ / 一致率与总体判定：
`calibrated`（judge 分数可进正式报告）或 `diagnostic_only`（未达门槛，见 rubric 文档的门槛表）。

## 关键规则

- **工作表不可手改内容**：只填 `annotations`；想换数据集（如真实模型输出）就重新生成，重新生成必须重标。
- **不一致不是错**：与 judge 不一致的条目暴露判据歧义 → 修订 rubric 文档 → judge prompt 版本升级 → 重新校准。
- **judge 模型换版本后必须重新校准**（judge 模型记录在分数文件的元数据里）。
