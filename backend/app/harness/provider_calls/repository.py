"""ProviderCall audit + frozen Fixture bundle repository."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider_call import (
    EvalProviderFixtureBundle,
    EvalProviderFixtureItem,
    ProviderCall,
)


class ProviderCallRepository:
    """CRUD over ``provider_calls`` / ``eval_provider_fixture_*`` tables."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- audit rows ---
    async def create(self, call: ProviderCall) -> ProviderCall:
        self._session.add(call)
        await self._session.flush()
        return call

    async def list_for_run(self, run_id: UUID) -> list[ProviderCall]:
        result = await self._session.execute(
            select(ProviderCall)
            .where(ProviderCall.run_id == run_id)
            .order_by(ProviderCall.sequence)
        )
        return list(result.scalars())

    async def list_for_trial(self, trial_id: UUID) -> list[ProviderCall]:
        result = await self._session.execute(
            select(ProviderCall)
            .where(ProviderCall.trial_id == trial_id)
            .order_by(ProviderCall.run_id, ProviderCall.sequence)
        )
        return list(result.scalars())

    async def clear_for_run(self, run_id: UUID) -> None:
        await self._session.execute(
            delete(ProviderCall).where(ProviderCall.run_id == run_id)
        )

    async def count_for_run(self, run_id: UUID) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(ProviderCall).where(
                ProviderCall.run_id == run_id
            )
        )
        return int(result.scalar_one())

    # --- fixtures ---
    async def create_bundle(
        self, *, trial_id: UUID, bundle_hash: str, fixture_count: int
    ) -> EvalProviderFixtureBundle:
        bundle = EvalProviderFixtureBundle(
            trial_id=trial_id,
            bundle_hash=bundle_hash,
            fixture_count=fixture_count,
        )
        self._session.add(bundle)
        await self._session.flush()
        return bundle

    async def create_fixture_item(
        self, item: EvalProviderFixtureItem
    ) -> EvalProviderFixtureItem:
        self._session.add(item)
        await self._session.flush()
        return item

    async def create_fixture_items(
        self, items: Sequence[EvalProviderFixtureItem]
    ) -> list[EvalProviderFixtureItem]:
        self._session.add_all(items)
        await self._session.flush()
        return list(items)

    async def list_bundles_for_trial(
        self, trial_id: UUID
    ) -> list[EvalProviderFixtureBundle]:
        result = await self._session.execute(
            select(EvalProviderFixtureBundle)
            .where(EvalProviderFixtureBundle.trial_id == trial_id)
            .order_by(EvalProviderFixtureBundle.created_at)
        )
        return list(result.scalars())

    async def list_fixture_items(
        self, bundle_id: UUID
    ) -> list[EvalProviderFixtureItem]:
        result = await self._session.execute(
            select(EvalProviderFixtureItem)
            .where(EvalProviderFixtureItem.bundle_id == bundle_id)
            .order_by(EvalProviderFixtureItem.sequence)
        )
        return list(result.scalars())
