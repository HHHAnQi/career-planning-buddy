# 安全与合规规范

## 1. 身份与数据隔离

- API 只信任服务端解析出的 `user_id`；
- Repository 查询必须带用户范围；
- Guest token 使用高强度 `JWT_SECRET`，生产环境必须轮换；
- 日志、Trace、Snapshot、Tool Fixture 和 Eval 数据集不得保存 token、密钥、完整简历或其他不必要隐私；
- input/config snapshot 只保存 Replay 所需的最小字段，采用字段级脱敏和保留期；
- tool_calls.result_json 最大 32KB，只保存清洗后的结构化结果。

## 2. Agent 输入安全

系统 Prompt、开发者规则、用户输入、搜索结果按信任等级分层。网页和检索内容永远是不可信数据，不能改变工具权限、系统规则或输出 Schema。

需要：

- 限制输入长度和附件类型；
- 对 Tool 参数做 Pydantic/JSON Schema 校验；
- 对检索内容加来源、类型和截断；
- evidence_ref 必须能解析到当前 Run/用户允许的真实资源；
- 禁止把网页中的“忽略之前规则”当成指令执行。

## 3. 高风险分流

`risk_gate` 在普通规划前执行。命中高风险条件时：

```text
risk_gate → safe_response → END
```

`safe_response` 使用评审过的模板，不让模型自由生成诊断或治疗建议。地区资源、紧急服务和热线信息必须从可更新配置读取，按用户明确地区选择；未知地区时只建议联系当地紧急服务、专业机构或可信赖的人，不硬编码号码。

高风险原文不写入长期记忆；Trace 只保存风险类别、规则 ID 和脱敏摘要。

## 4. 记忆安全

- 敏感候选记忆默认待确认；
- 用户可查看、删除和关闭记忆；
- 删除后向量索引同步失效；
- 记忆不得存密码、token、精确住址、医疗诊断等信息；
- 从搜索结果提取的事实与用户自述必须区分来源。

## 5. Provider 与密钥

运行时模型只通过环境变量接入 OpenAI-compatible Provider。代码和文档不得写死不存在的模型版本或把编码助手当作运行时依赖。

- 密钥不入库、不入日志、不入前端；
- 外部调用有 HTTPS、超时、重试上限和响应大小限制；
- Provider 失败时按 `error-handling-standard.md` 降级；
- Trace 记录实际 provider/model id，不记录密钥；
- Replay 默认使用保存 fixture，缺 fixture 不静默访问真实网络。

## 6. 内容边界

产品提供求职规划辅助，不保证就业结果，不替代职业、法律、医疗或心理健康专业服务。涉及高风险内容时优先安全分流，不继续生成普通求职计划。
