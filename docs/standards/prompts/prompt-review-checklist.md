# Prompt 评审检查表

每次 Prompt 变更在 PR 描述中勾选。未涉及的项注明 N/A，不要伪造执行结果。

## A. 格式与契约

- [ ] Prompt key 已列入 `runtime-prompt-matrix.md`
- [ ] System 只包含身份、硬约束、Tool/证据边界和输出契约
- [ ] 用户请求、画像、记忆和网页只进入不可信数据区
- [ ] 输出绑定明确 Pydantic Schema
- [ ] Tool Call 与 Final Result 的互斥规则明确
- [ ] Few-shot ≤2 且脱敏、与当前 Schema 一致

## B. 安全

- [ ] 无 API Key、JWT、手机号、密码等真实敏感数据
- [ ] evidence 使用明确 source_id 边界
- [ ] 明确外部文本不具有指令权限
- [ ] Agent 无写业务表 Tool
- [ ] high risk 分支不经过规划 Prompt

## C. 版本与快照

- [ ] 文件名为 `<purpose>_v<n>.py`
- [ ] 已被历史 Run 引用的版本未原地修改
- [ ] PromptRegistry 默认映射按需更新
- [ ] config snapshot 能记录实际 prompt key/version
- [ ] 旧版本文件仍可被 Replay 加载

## D. Repair 专项

- [ ] format repair 只修格式，不重跑 Tool
- [ ] business repair 只使用失败规则和最小上下文
- [ ] repair Tool 列表为空
- [ ] business repair 不改变 goal_type、completed facts 或新增 source_id
- [ ] 每类 repair 最多一次并计入全局预算

## E. Eval

真实 Prompt 接入后必须提供：

- [ ] 旧/新版本固定 Case 对比
- [ ] 总通过率和各 Grader diff
- [ ] 新增/修复/回归失败 Case
- [ ] Token、成本和延迟变化
- [ ] Tool 调用数量/来源完整率变化（如适用）

阈值由项目 Eval 配置定义，不在清单中硬编码一个对所有 Prompt 都合理的数值。

## F. Trace 与 Replay

- [ ] agent_steps 能记录 prompt key/version/model/usage
- [ ] Replay 可使用原 input/config snapshot
- [ ] 相同模型并不保证字面输出完全一致；比较以 Schema、规则指标和差异报告为准
- [ ] Tool fixture 缺失不会静默访问网络

## G. Spec 同步

- [ ] 对应 node spec、Runtime 预算和输出 Schema 已同步
- [ ] 若改变 Graph/Tool/状态机，已先修改权威设计并补测试
