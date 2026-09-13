# Stage 6A 验收报告

> 日期：2026-08-04  
> 分支：`feat/merge-dev-fixes-ly`  
> 范围：Memory Feedback Loop & Context Quality

## 已实现

- pinned 优先、语义相似度、时间衰减和字符预算组合的 Memory Selection；
- Embedding 异常时文本 fallback，检索异常时基础规划继续；
- 选中 Memory 的版本快照、`last_used_at`、脱敏 Trace 与 Plan evidence；
- Review 到 pending MemoryCandidate 的确定性、同事务、幂等闭环；
- 最近 5 Task / 2 Review 保留与更早历史确定性压缩；
- 稳定 XML 分区的 Prompt 隔离及 Stage 6 Prompt 版本；
- 冻结的 12 条 Stage 6A Mock Eval 数据集。

## 数据库与 API

- 数据库迁移：无。现有字段足以保存来源、规则版本和规范化摘要。
- 公网 API：无新增、无破坏性变化；现有 MemoryCandidate confirm/reject 契约复用。
- OpenAPI：运行 Snapshot 测试确认无变化。

## 明确未实现

- 真实 Web Search Provider；
- `SearchSource → ExperienceAtomCandidate` 的 `distill_evidence`；
- 在线强制 `quality_reviewer`、LLM-as-Judge、显式思维链；
- HyDE、Hybrid Search、Reranker、GPU 优化；
- Redis、多 Worker、MCP、多 Agent、可观测性平台。

## 自动化结果

完整验收结果：

- `docker compose config`：通过（输出已抑制，避免显示本地配置）；
- PostgreSQL：healthy；Alembic `20260731_0006 (head)`；
- Ruff：通过；
- Mypy：通过；
- 后端 Pytest：121/121 通过；
- Stage 5 Eval：30/30 通过；
- Stage 6A Eval：12/12 通过；
- 大历史 fixture：压缩比例达到至少 40% 的验收线；
- 前端 Vitest：6 files、13 tests 全部通过；
- 前端 production build：通过；
- `scripts/check.ps1`：通过；
- OpenAPI Snapshot：通过且无需更新。

## 真实 Provider 冒烟

未读取或输出本地 `.env`，也未获得一组由调用者显式提供的 DeepSeek 凭据和本地
BGE 路径，因此路线图列出的 5 个真实 Provider 场景未执行。自动化验收全部使用
Mock Provider；这项手动冒烟是本轮唯一未验证项。
