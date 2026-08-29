# DeepSeek 接入示例（可选）

DeepSeek 只是 OpenAI-compatible Provider 的一个适配示例，不是项目强绑定。具体模型 ID、价格、上下文长度和限额可能变化，接入时以厂商控制台和官方文档为准，不在设计文档中虚构版本名称。

## 配置示例

```env
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=your-secret
LLM_MODEL=deepseek-chat
```

## 验证清单

1. 最小聊天请求成功；
2. 结构化 JSON 可被 Pydantic 解析；
3. 超时和限流错误可识别；
4. usage 与 request id 可记录；
5. Tool Calling/JSON 模式若被使用，必须用当前真实接口验证；
6. 失败时能切回 Mock 或显式 degraded，不阻塞数据库闭环。

## 注意

- 不把 `DeepSeek V4` 等项目代号写入代码；
- 不在仓库提交 API Key；
- 生产参数必须由配置注入；
- 接入结果写入实际测试报告，而不是只写“支持”。
