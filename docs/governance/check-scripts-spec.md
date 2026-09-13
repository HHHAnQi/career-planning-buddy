# 门禁脚本规范

Stage 0 创建 `scripts/check.sh`，作为本地和 CI 的统一入口。

## 第一版检查项

```bash
#!/usr/bin/env bash
set -euo pipefail

cd backend
ruff check .
ruff format --check .
mypy app
pytest

cd ../frontend
npm run lint
npm test -- --run
npm run build
```

## 后续可增加

- Alembic 单头检查与空数据库升级；
- OpenAPI snapshot 对比；
- Markdown 内部链接检查；
- 密钥扫描；
- 依赖漏洞扫描；
- Eval 冒烟集。

## 原则

- CI 不允许把主门禁设为 `allow_failure`；
- 门禁失败必须返回非零退出码；
- 脚本只检查仓库中真实存在的目录和命令；
- 尚未实现的高级检查不得在文档中宣称已完成。
