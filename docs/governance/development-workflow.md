# 开发流程

## 1. 领取任务

先写清楚：用户价值、输入输出、受影响 API/表/状态机、非目标和验收方式。跨模块任务在 `docs/requirements/<feature>/` 新建 `clarify.md`、`plan.md`、`tasks.md`。

## 2. 更新设计

根据变更类型更新：

- API：`docs/model-design/api-spec/`
- 数据表：`docs/model-design/data-models/`
- Agent 节点：`docs/model-design/agent-nodes/`
- 状态机：`docs/model-design/state-machines/`
- 全局技术决策：`docs/architecture/adr.md`

## 3. 让编码助手实现

每次提示词必须包含：

1. 阅读 `AGENTS.zh-CN.md`；
2. 阅读 `docs/implementation/project-baseline.md`；
3. 阅读当前阶段任务书和相关 spec；
4. 先输出影响文件和实现计划；
5. 实现代码、迁移、测试；
6. 执行验收命令并报告真实结果。

禁止要求“根据全部文档一次写完项目”。

## 4. 本地验证

至少执行：

```bash
bash scripts/check.sh
cd backend && pytest
cd ../frontend && npm test && npm run build
```

涉及迁移时再执行：

```bash
cd backend && alembic upgrade head
```

## 5. 代码评审

评审顺序：契约与状态机 → 用户隔离和事务 → Agent 边界 → 异常与降级 → 测试 → 可观测性。生成式代码必须逐文件审查，不能只看能否启动。

## 6. 合并

合并前确认：

- spec 与实现一致；
- 新增迁移可升级；
- 测试通过；
- 没有提交密钥、真实隐私数据或本地环境文件；
- README/任务书中的命令可复现。
