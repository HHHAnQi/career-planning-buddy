"""Developer-only cross-user Trace queries."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentEvent, AgentRun, AgentStep, ToolCall
from app.models.provider_call import ProviderCall


class DevTraceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_runs(
        self,
        *,
        status: str | None,
        result_kind: str | None,
        error_code: str | None,
        cursor: UUID | None,
        limit: int,
    ) -> list[AgentRun]:
        statement = select(AgentRun)
        if status is not None:
            statement = statement.where(AgentRun.status == status)
        if result_kind is not None:
            statement = statement.where(AgentRun.result_kind == result_kind)
        if error_code is not None:
            statement = statement.where(AgentRun.error_code == error_code)
        if cursor is not None:
            cursor_created_at = await self._session.scalar(
                select(AgentRun.created_at).where(AgentRun.id == cursor)
            )
            if cursor_created_at is not None:
                statement = statement.where(AgentRun.created_at < cursor_created_at)
        rows = await self._session.scalars(
            statement.order_by(AgentRun.created_at.desc()).limit(limit + 1)
        )
        return list(rows)

    async def get_run(self, run_id: UUID) -> AgentRun | None:
        return await self._session.get(AgentRun, run_id)

    async def get_trace(
        self, run_id: UUID
    ) -> tuple[list[AgentStep], list[ToolCall], list[AgentEvent]]:
        steps = list(
            await self._session.scalars(
                select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.sequence)
            )
        )
        tools = list(
            await self._session.scalars(
                select(ToolCall)
                .where(ToolCall.run_id == run_id)
                .order_by(ToolCall.created_at, ToolCall.id)
            )
        )
        events = list(
            await self._session.scalars(
                select(AgentEvent).where(AgentEvent.run_id == run_id).order_by(AgentEvent.sequence)
            )
        )
        return steps, tools, events

    async def usage_runs(self, *, since: datetime) -> list[AgentRun]:
        """All Runs created inside the reporting window, oldest first."""

        rows = await self._session.scalars(
            select(AgentRun)
            .where(AgentRun.created_at >= since)
            .order_by(AgentRun.created_at, AgentRun.id)
        )
        return list(rows)

    async def usage_provider_calls(self, *, since: datetime) -> list[tuple[str, str, int, int]]:
        """(provider_kind, status, call_count, avg_latency_ms) groups."""

        rows = await self._session.execute(
            select(
                ProviderCall.provider_kind,
                ProviderCall.status,
                func.count().label("call_count"),
                func.avg(ProviderCall.latency_ms).label("avg_latency_ms"),
            )
            .where(ProviderCall.created_at >= since)
            .group_by(ProviderCall.provider_kind, ProviderCall.status)
            .order_by(ProviderCall.provider_kind, ProviderCall.status)
        )
        return [
            (kind, status, int(count), int(avg_latency or 0))
            for kind, status, count, avg_latency in rows
        ]
