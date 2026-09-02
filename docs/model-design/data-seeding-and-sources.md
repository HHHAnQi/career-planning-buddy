# 数据与知识来源设计

## 1. 目标

为 AI/后端/Agent 求职规划提供小而可信的经验库，不追求海量岗位爬取。

## 2. 数据类型

| 类型 | 位置 | 用途 |
|---|---|---|
| 用户画像 | user_profiles | 目标与约束 |
| 执行事实 | tasks/reviews | 复盘与重规划 |
| 长期记忆 | memories | 个性化上下文 |
| 搜索快照 | search_sources | 动态事实与来源 |
| 经验原子 | experience_atoms | RAG 规划依据 |
| Eval Case | backend/evals/datasets/*.jsonl | 质量回归 |

## 3. 经验原子格式

```json
{
  "goal_type": "agent_app",
  "title": "Agent 项目应展示运行 Trace",
  "content": "为一次真实 Run 展示节点、Tool、Token、耗时和失败原因。",
  "evidence": {
    "source_type": "manual_reviewed",
    "source_url": null,
    "confidence": 0.85,
    "applicable_stage": ["preparing", "applying"]
  }
}
```

## 4. 首批种子数据

- 30~50 条人工审核经验原子；
- 覆盖 agent_app、ai_backend、backend_java；
- 每条有适用阶段和置信度；
- 不直接复制长篇网页；
- 不把社区意见冒充官方事实。

## 5. 检索

```text
query = 用户消息 + goal_type + stage + 当前 blocker
filter = goal_type + is_active
retrieve top_k=8
rerank/规则筛选 top_n=3~5
```

MVP 可先用 pgvector cosine + metadata filter；数据量小时无需 IVFFlat，避免索引训练样本不足。达到一定规模后再建立 HNSW/IVFFlat，并用 P95 验证。

## 6. Search 入库

SearchProvider 结果必须清洗：URL、标题、摘要、来源类型、检索时间、可信度。模型只能引用已入库 source id。

## 7. 数据质量

- 去重：标准化 URL + 内容 hash；
- 过期：动态招聘信息设置 freshness；
- 冲突：同时保留并在 evidence 标注；
- 删除：用户数据可按用户级联清理；
- 敏感：不进入经验库。
