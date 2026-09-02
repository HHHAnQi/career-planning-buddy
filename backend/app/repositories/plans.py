"""User-scoped Plan and Task persistence operations."""

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import product_today
from app.models.plan import CompanionMessage, Plan, Task


class PlanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_user(
        self, plan_id: UUID, user_id: UUID, *, for_update: bool = False
    ) -> Plan | None:
        statement = select(Plan).where(Plan.id == plan_id, Plan.user_id == user_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_active_for_user(self, user_id: UUID, *, for_update: bool = False) -> Plan | None:
        statement = (
            select(Plan)
            .where(Plan.user_id == user_id, Plan.status.in_(("generated", "active")))
            .order_by(Plan.created_at.desc())
            .limit(1)
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_current_cycle_for_user(self, user_id: UUID) -> Plan | None:
        """Return the Plan that should be visible today without changing domain state.

        Archived Plans are considered only while their fixed cycle still contains
        today. This read-only compatibility branch recovers legacy data where an
        early completion archived the current cycle and created a future Plan.
        """
        today = product_today()
        cycle_end = func.least(Plan.plan_date + 6, Plan.horizon_end)
        result = await self._session.execute(
            select(Plan)
            .where(
                Plan.user_id == user_id,
                Plan.plan_date <= today,
                or_(
                    Plan.status.in_(("generated", "active", "completed")),
                    ((Plan.status == "archived") & (cycle_end >= today)),
                ),
            )
            .order_by(
                case(
                    (Plan.status.in_(("generated", "active")), 0),
                    (Plan.status == "completed", 1),
                    else_=2,
                ),
                Plan.plan_date.desc(),
                Plan.created_at.desc(),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_latest_completed_for_user(self, user_id: UUID) -> Plan | None:
        result = await self._session.execute(
            select(Plan)
            .where(Plan.user_id == user_id, Plan.status == "completed")
            .order_by(Plan.completed_at.desc().nullslast(), Plan.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def archive(self, plan: Plan) -> None:
        plan.status = "archived"
        plan.archived_at = datetime.now(UTC)
        plan.updated_at = datetime.now(UTC)
        plan.version += 1
        await self._session.flush()

    async def create_plan(self, values: dict[str, object]) -> Plan:
        plan = Plan(**values)
        self._session.add(plan)
        await self._session.flush()
        await self._session.refresh(plan)
        return plan

    async def create_tasks(
        self, *, plan_id: UUID, user_id: UUID, candidates: list[dict[str, object]]
    ) -> list[Task]:
        tasks = [
            Task(plan_id=plan_id, user_id=user_id, order_index=index, **candidate)
            for index, candidate in enumerate(candidates)
        ]
        self._session.add_all(tasks)
        await self._session.flush()
        return tasks

    async def create_companion(
        self,
        *,
        user_id: UUID,
        run_id: UUID | None = None,
        plan_id: UUID | None = None,
        task_id: UUID | None = None,
        review_id: UUID | None = None,
        trigger_tag: str,
        message: str,
        template_version: str,
    ) -> CompanionMessage:
        companion = CompanionMessage(
            user_id=user_id,
            run_id=run_id,
            plan_id=plan_id,
            task_id=task_id,
            review_id=review_id,
            trigger_tag=trigger_tag,
            message=message,
            template_version=template_version,
        )
        self._session.add(companion)
        await self._session.flush()
        return companion

    async def get_task_for_user(
        self,
        task_id: UUID,
        user_id: UUID,
        *,
        for_update: bool = False,
    ) -> Task | None:
        statement = select(Task).where(Task.id == task_id, Task.user_id == user_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def task_state_counts(
        self,
        *,
        user_id: UUID,
        plan_id: UUID,
        scheduled_date: date | None,
    ) -> dict[str, int]:
        statement = select(Task.state, func.count(Task.id)).where(
            Task.user_id == user_id,
            Task.plan_id == plan_id,
        )
        if scheduled_date is not None:
            statement = statement.where(Task.scheduled_date == scheduled_date)
        result = await self._session.execute(statement.group_by(Task.state))
        return {state: count for state, count in result.all()}

    async def recent_task_states(self, user_id: UUID, *, limit: int = 2) -> list[str]:
        result = await self._session.execute(
            select(Task.state)
            .where(Task.user_id == user_id)
            .order_by(Task.updated_at.desc(), Task.id.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def recent_tasks(
        self,
        user_id: UUID,
        *,
        limit: int = 30,
    ) -> list[Task]:
        result = await self._session.execute(
            select(Task)
            .where(Task.user_id == user_id)
            .order_by(
                case(
                    (Task.state == "abandoned", 0),
                    (Task.state == "completed", 1),
                    (Task.state == "in_progress", 2),
                    else_=3,
                ),
                Task.updated_at.desc(),
                Task.id.desc(),
            )
            .limit(limit)
        )
        return list(result.scalars())

    async def list_plans(
        self,
        user_id: UUID,
        *,
        status: str | None,
        date_from: date | None,
        date_to: date | None,
        cursor: UUID | None,
        limit: int,
    ) -> list[Plan]:
        statement = select(Plan).where(Plan.user_id == user_id)
        if status is not None:
            statement = statement.where(Plan.status == status)
        if date_from is not None:
            statement = statement.where(Plan.plan_date >= date_from)
        if date_to is not None:
            statement = statement.where(Plan.plan_date <= date_to)
        if cursor is not None:
            cursor_plan = await self.get_for_user(cursor, user_id)
            if cursor_plan is None:
                return []
            statement = statement.where(
                or_(
                    Plan.created_at < cursor_plan.created_at,
                    ((Plan.created_at == cursor_plan.created_at) & (Plan.id < cursor_plan.id)),
                )
            )
        result = await self._session.execute(
            statement.order_by(Plan.created_at.desc(), Plan.id.desc()).limit(limit)
        )
        return list(result.scalars())

    async def list_tasks(
        self,
        user_id: UUID,
        *,
        scheduled_date: date | None = None,
        state: str | None = None,
        plan_id: UUID | None = None,
        limit: int = 50,
    ) -> list[Task]:
        statement = select(Task).where(Task.user_id == user_id)
        if scheduled_date is not None:
            statement = statement.where(Task.scheduled_date == scheduled_date)
        if state is not None:
            statement = statement.where(Task.state == state)
        if plan_id is not None:
            statement = statement.where(Task.plan_id == plan_id)
        result = await self._session.execute(
            statement.order_by(Task.scheduled_date, Task.order_index).limit(limit)
        )
        return list(result.scalars())

    async def tasks_for_plan(self, plan_id: UUID, user_id: UUID) -> list[Task]:
        return await self.list_tasks(user_id, plan_id=plan_id, limit=100)

    async def companion_for_plan(self, plan_id: UUID, user_id: UUID) -> CompanionMessage | None:
        result = await self._session.execute(
            select(CompanionMessage)
            .where(
                CompanionMessage.plan_id == plan_id,
                CompanionMessage.user_id == user_id,
            )
            .order_by(CompanionMessage.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def recent_completed_deliverables(self, user_id: UUID) -> list[str]:
        result = await self._session.execute(
            select(Task.deliverable)
            .where(Task.user_id == user_id, Task.state == "completed")
            .order_by(Task.completed_at.desc().nullslast())
            .limit(20)
        )
        return list(result.scalars())

    async def archive_other_active(self, user_id: UUID, keep_id: UUID | None) -> None:
        statement = update(Plan).where(
            Plan.user_id == user_id,
            Plan.status.in_(("generated", "active")),
        )
        if keep_id is not None:
            statement = statement.where(Plan.id != keep_id)
        await self._session.execute(
            statement.values(
                status="archived",
                archived_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                version=Plan.version + 1,
            )
        )
