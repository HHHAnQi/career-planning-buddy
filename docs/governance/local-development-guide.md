# 本地开发指南

## 1. 目标环境

- Python 3.12
- Node.js 22
- Docker Desktop / Docker Engine
- PostgreSQL 16 + pgvector（由 Docker Compose 启动）

Windows 用户建议统一在 PowerShell 或 WSL 中操作同一仓库，避免交替使用导致换行符和权限问题。

## 2. 初始化（Stage 0 完成后）

macOS/Linux：

```bash
cp .env.example .env
docker compose up -d postgres

python3.12 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.lock
backend/.venv/bin/python -m pip install --no-deps --editable backend

cd backend
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m uvicorn app.main:app --reload
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
docker compose up -d postgres

py -3.12 -m venv backend\.venv
backend\.venv\Scripts\python -m pip install -r backend\requirements.lock
backend\.venv\Scripts\python -m pip install --no-deps --editable backend

Set-Location backend
.venv\Scripts\python -m alembic upgrade head
.venv\Scripts\python -m uvicorn app.main:app --reload
```

新终端启动前端：

```bash
cd frontend
npm ci
npm run dev
```

## 3. Provider 配置

Mock Provider 是默认本地路径，不需要任何外部密钥。接真实模型时只填写环境变量：

```env
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://example.com/v1
LLM_API_KEY=...
LLM_MODEL=实际模型ID
```

不要把 Codex 或其他编码助手名称写入运行时配置；项目运行时模型通过 Provider 环境变量独立配置。

## 4. 常用检查

```bash
bash scripts/check.sh
cd backend && .venv/bin/python -m pytest
cd ../frontend && npm test && npm run build
```

## 5. 常见问题

- 数据库连接失败：先检查 `docker compose ps` 和 `DATABASE_URL`。
- SSE 无事件：确认 Run 已创建、`agent_events` 有记录，并检查浏览器 Network。
- 模型不可用：切回 Mock Provider，先验证业务纵切，不要阻塞工程开发。
- 迁移冲突：不要手改数据库结构，修复 Alembic revision 后重建本地库。
- 门禁提示缺少 `.venv`：按初始化步骤安装锁定依赖；只有受控环境才应显式设置 `BACKEND_PYTHON`。
- 本地门禁默认使用 `career_buddy_test`，不会把测试数据写入开发库。
