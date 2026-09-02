"""Serializable per-node checkpoints for bounded Agent recovery."""

import json
from hashlib import sha256
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import session_transaction
from app.models.agent_run import AgentCheckpoint


class CheckpointStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def load(self, run_id: UUID, node_name: str) -> dict[str, object] | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(AgentCheckpoint)
                .where(
                    AgentCheckpoint.run_id == run_id,
                    AgentCheckpoint.node_name == node_name,
                )
                .order_by(AgentCheckpoint.attempt.desc())
                .limit(1)
            )
            return dict(row.state_json) if row is not None else None

    async def save(
        self,
        run_id: UUID,
        attempt: int,
        node_name: str,
        state: dict[str, object],
    ) -> None:
        encoded = json.dumps(
            state, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        async with self._sessions() as session:
            async with session_transaction(session):
                existing = await session.scalar(
                    select(AgentCheckpoint).where(
                        AgentCheckpoint.run_id == run_id,
                        AgentCheckpoint.attempt == attempt,
                        AgentCheckpoint.node_name == node_name,
                    )
                )
                if existing is not None:
                    if existing.state_hash != sha256(encoded).hexdigest():
                        raise RuntimeError("checkpoint output changed within one attempt")
                    return
                session.add(
                    AgentCheckpoint(
                        run_id=run_id,
                        attempt=attempt,
                        node_name=node_name,
                        state_json=state,
                        state_hash=sha256(encoded).hexdigest(),
                    )
                )
