# Stage 0：工程基线

## 目标

创建可启动、可测试、可迁移的前后端骨架，不实现业务功能。

## Codex 执行任务

请先阅读 `AGENTS.zh-CN.md` 与 `docs/implementation/project-baseline.md`，然后完成：

### 后端

- 创建 `backend/pyproject.toml`；
- 创建 FastAPI 应用和 `/health`；
- 创建 Pydantic Settings；
- 创建 SQLAlchemy Async Engine 与 Session；
- 初始化 Alembic；
- 创建结构化日志和统一异常基类；
- 创建 pytest 基线。

### 前端

- React + TypeScript + Vite；
- React Router；
- TanStack Query；
- `/` 页面显示项目名与后端健康状态。

### 基础设施

- PostgreSQL 16 + pgvector Docker Compose；
- `.env.example`；
- `scripts/check.sh`；
- GitHub Actions 或等价 CI。

## 不做

- 不创建 Agent Graph；
- 不接真实模型；
- 不创建业务表；
- 不引入 Redis/Celery。

## 验收

```bash
docker compose up -d postgres
cd backend && pytest
uvicorn app.main:app --reload
curl http://localhost:8000/health
cd ../frontend && npm test && npm run build
```

预期：健康检查 200、后端测试通过、前端构建通过。
