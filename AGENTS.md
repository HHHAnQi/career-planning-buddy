# AI Coding Rules

This repository is an independent greenfield project. It does not use ClawAgent as a base and must not import or assume any ClawAgent module.

Before coding, read in this order:

1. `docs/implementation/project-baseline.md`
2. the current `docs/implementation/stage-*.md`
3. the relevant API, data-model, state-machine, agent-runtime, tool, and node specs

Implement one stage or vertical slice at a time. First provide the intended files, database changes, API changes, and tests. Then implement and run the acceptance commands.

Core constraints:

- Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 Async, PostgreSQL 16.
- Runtime LLM access goes through Provider protocols; the coding assistant is not the runtime model.
- No Redis, Celery, MCP, multi-agent framework, object storage, or microservices in the MVP unless the baseline is explicitly revised.
- Routers handle HTTP only; Services own use cases and state transitions; Repositories own persistence.
- Agent nodes do not write ORM entities directly.
- Stage 2-4 Agent work must follow `docs/model-design/agent-runtime/README.md` and `docs/model-design/tools/README.md`.
- A Run has exactly one terminal event; snapshots and result_kind are part of the contract.
- Identity comes from JWT claims, never from request `user_id`.
- Persist SSE events to `agent_events` before streaming them.
- Validate structured LLM output with Pydantic; allow at most one formatting repair.
- Add tests for schemas, services, repositories, APIs, and deterministic agent nodes.
- Report changed files, commands run, results, and unresolved items. Do not silently continue into the next stage.
