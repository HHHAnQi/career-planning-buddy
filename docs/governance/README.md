# governance/ 目录入口

本目录定义人和编码助手如何在仓库中工作。它只描述流程，不覆盖产品、架构或接口事实。

## 文档地图

- [AI 阅读指南](./ai-reading-guide.md)
- [开发流程](./development-workflow.md)
- [本地开发指南](./local-development-guide.md)
- [Spec-Driven 工作流](./spec-driven-workflow.md)
- [阶段交付定义](./stage-delivery-definition.md)
- [新增用例检查表](./use-case-development-checklist.md)
- [验证与评审](./verification-and-review.md)
- [门禁脚本规范](./check-scripts-spec.md)

## 工作原则

1. 先确认业务用例，再改代码。
2. 接口、表结构、状态机变更必须先更新对应 spec。
3. 一次只执行一个实现阶段，不让编码助手整仓生成。
4. 每个阶段必须有可运行验收，不以“文件已生成”作为完成。
5. 文档冲突时，以根 README 中的权威顺序为准。
