# Security Policy

## 支持范围

安全修复以默认分支的最新版本为准。历史分支、历史 Review 文档和本地定制 Provider 配置不作为单独维护版本。

## 报告安全问题

请优先使用 GitHub 仓库的 **Security → Report a vulnerability** 私密报告入口。不要在公开 Issue、Discussion、Pull Request 或截图中提交：

- API Key、JWT Secret、数据库口令或完整请求 Header；
- 真实简历、目标 JD、面试回答、音频或个人记忆；
- 可直接利用的攻击步骤与尚未修复的用户数据样本。

报告中请包含受影响版本、复现条件、实际影响和经过脱敏的最小证据。如果无法使用 Private Vulnerability Reporting，可先创建一个不包含漏洞细节的普通 Issue，请维护者提供私密联系渠道。

## Secret 泄漏处理

如果凭据曾经进入 Git、日志、截图或第三方系统，仅删除文件是不够的：

1. 立即在 Provider 或部署平台撤销并轮换凭据；
2. 检查 Actions、部署日志、Release Artifact 和 Fork；
3. 在确认影响后再决定是否需要清理 Git 历史；
4. 使用新凭据重启后端，使缓存的 Settings 和 Provider Client 失效。

## 项目安全边界

- `.env` 和常见本地 Secret 文件默认被 Git 忽略；
- Provider 凭据只保存在后端，禁止放入 `VITE_*`；
- JWT Claims 是用户身份事实源；
- 开发者接口需要服务端持久化角色校验；
- SSE 不使用查询参数传递 Token；
- L2 个人记忆按用户隔离且需要确认；L3 共享知识需要来源和人工审核；
- 健康检查只验证配置完整性，不调用计费 API。

本项目默认面向本地单机部署，不应直接作为承载真实求职材料的公共 SaaS 使用。生产部署前请完成仓库中的生产就绪审查，并配置独立 Secret 管理、备份、监控、TLS、数据保留与删除策略。
