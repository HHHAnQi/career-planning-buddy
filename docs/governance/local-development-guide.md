# 本地开发指南

## 1. 目标环境

- Python 3.12
- Node.js 20+
- Docker Desktop / Docker Engine
- PostgreSQL 16 + pgvector（由 Docker Compose 启动）

Windows 用户建议统一在 PowerShell 或 WSL 中操作同一仓库，避免交替使用导致换行符和权限问题。

## 2. 初始化（Stage 0 完成后）

```bash
cp .env.example .env
docker compose up -d postgres

cd backend
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -e '.[dev]'
alembic upgrade head
uvicorn app.main:app --reload
```

新终端启动前端：

```bash
cd frontend
npm install
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
cd backend && pytest
cd ../frontend && npm test && npm run build
```

## 5. 常见问题

- 数据库连接失败：先检查 `docker compose ps` 和 `DATABASE_URL`。
- SSE 无事件：确认 Run 已创建、`agent_events` 有记录，并检查浏览器 Network。
- 模型不可用：切回 Mock Provider，先验证业务纵切，不要阻塞工程开发。
- 迁移冲突：不要手改数据库结构，修复 Alembic revision 后重建本地库。
