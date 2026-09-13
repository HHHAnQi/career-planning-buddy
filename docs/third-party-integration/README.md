# 第三方集成

本项目只保留三类外部 Provider：

| Provider | MVP 阶段 | 用途 |
|---|---|---|
| LLMProvider | Stage 3 | 意图、规划、结构化生成 |
| SearchProvider | Stage 4 | 获取带来源的公开求职信息 |
| EmbeddingProvider | Stage 4 | 经验原子和记忆向量化 |

所有 Provider 先实现 Mock，再实现真实适配器。接口定义以 [架构 ADR](../architecture/adr.md) 和 [实现基线](../implementation/project-baseline.md) 为准。

- [OpenAI-compatible LLM 接入](./llm-provider.md)
- [DeepSeek 适配示例](./deepseek-api.md)

Codex 属于开发工具，不属于 Provider 架构，也不得被写入项目运行时依赖。
