#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -x "$repository_root/backend/.venv/bin/python" ]]; then
  backend_python="$repository_root/backend/.venv/bin/python"
elif [[ -x "$repository_root/backend/.venv/Scripts/python.exe" ]]; then
  backend_python="$repository_root/backend/.venv/Scripts/python.exe"
else
  backend_python="python"
fi

pushd "$repository_root/backend" >/dev/null
export APP_ENV="test"
export LLM_PROVIDER="mock"
export SEARCH_PROVIDER="mock"
export EMBEDDING_PROVIDER="mock"
export EVAL_PROVIDER_MODE="mock"
export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://career_buddy:career_buddy_local@localhost:5432/career_buddy}"
export JWT_SECRET="${JWT_SECRET:-local-test-secret-with-at-least-32-characters}"
if [[ "$backend_python" == *.exe ]]; then
  stage5_wsl_vars="APP_ENV:LLM_PROVIDER:SEARCH_PROVIDER:EMBEDDING_PROVIDER:EVAL_PROVIDER_MODE:DATABASE_URL:JWT_SECRET"
  export WSLENV="${WSLENV:+$WSLENV:}$stage5_wsl_vars"
fi
"$backend_python" -m ruff check .
"$backend_python" -m mypy app tests scripts evals
"$backend_python" -m alembic upgrade head
"$backend_python" -m alembic check
"$backend_python" -m pytest
"$backend_python" -m scripts.run_eval --no-persist
"$backend_python" -m evals.v2 run --dataset stage5 \
  --provider-mode mock --trial-count 1 --require-all-hard-gates
popd >/dev/null

pushd "$repository_root/frontend" >/dev/null
npm test
npm run build
popd >/dev/null
