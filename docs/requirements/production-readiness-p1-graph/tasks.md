# P1-2 Agent Graph 纯逻辑边界：任务与验证

- [x] 提取规划输入指纹。
- [x] 提取 checkpoint payload 序列化与类型化恢复。
- [x] 提取 Provider usage 解析、合并与遥测构建。
- [x] checkpoint 恢复不在解码期间修改 Graph State。
- [x] 增加纯逻辑单元测试和依赖方向守卫。
- [x] 聚焦回归：49 passed。
- [x] 全量后端回归：827 passed；1 个 usage-report 用例受本地复用测试库的 30 天外历史数据影响，交由干净 CI 数据库复验。
- [ ] 远程 CI 验证。
