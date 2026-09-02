# Prompt 格式规范

## 1. 消息分层

Provider 接收标准 messages：system 放身份和不可协商约束，user 放任务和业务输入。外部搜索结果、记忆和用户文本均视为不可信数据，不得拼入 system 指令区。

## 2. 结构化输出

每次需要机器消费的输出都绑定 Pydantic `response_model`。Provider 可使用厂商原生 JSON Schema 能力，也可要求 JSON 后解析；无论实现方式，业务层只接收验证后的对象。

Schema 失败最多修复一次，仍失败按节点规则降级或失败。禁止用正则从自由文本“捞 JSON”作为主路径。

## 3. Evidence 包装

检索内容使用明确边界并附来源，例如：

```text
<evidence source_id="..." trust="medium">
清洗、截断后的公开资料
</evidence>
```

Prompt 必须声明 evidence 只提供事实，不具有指令权限。

## 4. Few-shot

最多 2 个脱敏示例。示例必须与当前 Schema 一致，不得包含真实用户信息。

## 5. 预算

每个节点在 spec 中写清输入上限、输出上限、超时和是否允许修复。全局上限以 `project-baseline.md` 为准，节点不得自行扩大。
