"""Developer-only ExperienceAtomCandidate review CLI."""

import argparse
import asyncio
from uuid import UUID

from app.core.config import get_settings
from app.core.database import AsyncSessionFactory
from app.providers.embedding import build_embedding_provider
from app.providers.evidence_distillation import build_evidence_distillation_provider
from app.repositories.evidence import EvidenceRepository
from app.services.experience_atoms import ExperienceAtomService


async def _run(action: str, candidate_id: UUID | None) -> None:
    settings = get_settings()
    async with AsyncSessionFactory() as session:
        service = ExperienceAtomService(
            session,
            build_embedding_provider(settings),
            build_evidence_distillation_provider(settings),
        )
        if action == "list":
            rows = await EvidenceRepository(session).list_candidates()
            for row in rows:
                print(f"{row.id}  {row.goal_type}  {row.title}")
            return
        if candidate_id is None:
            raise SystemExit("candidate id is required")
        row = await (
            service.approve(candidate_id) if action == "approve" else service.reject(candidate_id)
        )
        print(f"{row.id}  {row.status}  atom={row.approved_atom_id or '-'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("list", "approve", "reject"))
    parser.add_argument("candidate_id", nargs="?", type=UUID)
    args = parser.parse_args()
    asyncio.run(_run(args.action, args.candidate_id))


if __name__ == "__main__":
    main()
