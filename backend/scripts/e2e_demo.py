"""Reproducible Stage 5 end-to-end HTTP and local RAG demonstration."""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import UUID, uuid4

import httpx

from app.core.config import get_settings
from app.core.database import AsyncSessionFactory, session_transaction
from app.models.evidence import ExperienceAtom, MemoryCandidate
from app.providers.embedding import build_embedding_provider
from app.schemas.enums import GoalType, RunIntent
from app.tools.contracts import RagRetrieveInput, ToolContext
from app.tools.executors import RagRetrieveHandler

TERMINAL = {"completed", "degraded", "failed", "cancelled"}


async def _poll_run(client: httpx.AsyncClient, token: str, run_id: str) -> dict[str, object]:
    deadline = monotonic() + 180
    while monotonic() < deadline:
        response = await client.get(
            f"/api/v1/agent-runs/{run_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise RuntimeError("Run response was not an object")
        if body.get("status") in TERMINAL:
            return body
        await asyncio.sleep(0.2)
    raise TimeoutError(f"Run {run_id} did not converge within 180 seconds")


async def run_demo(base_url: str) -> dict[str, object]:
    settings = get_settings()
    suffix = uuid4().hex
    started = monotonic()
    async with httpx.AsyncClient(base_url=base_url, timeout=60, trust_env=False) as client:
        login = await client.post("/api/v1/auth/guest", json={"device_id": f"stage5-demo-{suffix}"})
        login.raise_for_status()
        login_body = login.json()
        token = str(login_body["access_token"])
        user_id = UUID(str(login_body["user"]["id"]))
        auth = {"Authorization": f"Bearer {token}"}

        profile = await client.put(
            "/api/v1/profile",
            headers={**auth, "Idempotency-Key": f"profile-{suffix}"},
            json={
                "goal_type": "agent_app",
                "stage": "preparing",
                "time_budget_minutes": 75,
                "skill_level": "intermediate",
                "skill_summary": "Python backend and basic LLM application experience",
                "deadline": None,
                "preferences": {
                    "target_companies": ["Stage 5 Demo Company"],
                    "preferred_time_slot": "evening",
                    "weekly_available_days": [1, 2, 3, 4, 5],
                },
            },
        )
        profile.raise_for_status()

        create = await client.post(
            "/api/v1/agent-runs",
            headers={**auth, "Idempotency-Key": f"create-{suffix}"},
            json={
                "message": "Create an executable Agent application job preparation plan",
                "hint_intent": "create_plan",
            },
        )
        create.raise_for_status()
        create_run_id = str(create.json()["run_id"])
        create_result = await _poll_run(client, token, create_run_id)
        if create_result.get("result_kind") != "plan":
            raise RuntimeError(f"create plan did not return a plan: {create_result.get('status')}")
        plan_id = str(create_result["final_plan_id"])

        plan = await client.get(f"/api/v1/plans/{plan_id}", headers=auth)
        plan.raise_for_status()
        tasks = plan.json()["tasks"]
        if not tasks:
            raise RuntimeError("generated plan did not contain tasks")
        first_task = tasks[0]
        in_progress = await client.patch(
            f"/api/v1/tasks/{first_task['task_id']}",
            headers=auth,
            json={"state": "in_progress", "version": first_task["version"]},
        )
        in_progress.raise_for_status()
        current_task = in_progress.json()["task"]
        completed = await client.patch(
            f"/api/v1/tasks/{first_task['task_id']}",
            headers=auth,
            json={
                "state": "completed",
                "version": current_task["version"],
                "actual_minutes": 35,
            },
        )
        completed.raise_for_status()

        review = await client.post(
            "/api/v1/reviews",
            headers={**auth, "Idempotency-Key": f"review-{suffix}"},
            json={
                "plan_id": plan_id,
                "review_date": datetime.now(UTC).date().isoformat(),
                "mood": 4,
                "blockers": "The original scope was too broad for one evening",
                "adjustment_request": "Reduce scope while retaining the completed evidence",
                "free_text": "The first concrete deliverable was completed.",
            },
        )
        review.raise_for_status()
        review_body = review.json()
        next_plan = await client.post(
            f"/api/v1/reviews/{review_body['review_id']}/start-next-plan",
            headers={**auth, "Idempotency-Key": f"replan-{suffix}"},
        )
        next_plan.raise_for_status()
        replan_run_id = str(next_plan.json()["run_id"])
        replan_result = await _poll_run(client, token, replan_run_id)
        if replan_result.get("result_kind") != "plan":
            raise RuntimeError(f"replan did not return a plan: {replan_result.get('status')}")

        embedding = build_embedding_provider(settings)
        memory_text = "Prefers small evening tasks with one reviewable deliverable."
        atom_text = (
            "For Agent application interviews, build a minimal traced workflow with "
            "schema validation, replay fixtures, and an evaluation report. "
            f"Deterministic demo marker: {suffix}."
        )
        memory_vector, atom_vector = await embedding.embed([memory_text, atom_text])
        async with AsyncSessionFactory() as session:
            async with session_transaction(session):
                candidate = MemoryCandidate(
                    user_id=user_id,
                    memory_type="stable_preference",
                    summary=memory_text,
                    content_json={"preferred_task_shape": "small_reviewable_evening_task"},
                    sensitivity="sensitive",
                    status="pending",
                    proposed_by_run_id=UUID(replan_run_id),
                    expires_at=datetime.now(UTC) + timedelta(days=7),
                )
                atom = ExperienceAtom(
                    goal_type="agent_app",
                    title=f"Stage 5 traced Agent workflow {suffix[:8]}",
                    content=atom_text,
                    evidence_json={"source": "stage5-e2e-demo", "reliability": 0.95},
                    embedding=atom_vector,
                )
                session.add_all([candidate, atom])
                await session.flush()
                candidate_id = candidate.id
                atom_id = atom.id

        confirmed = await client.post(
            f"/api/v1/memory-candidates/{candidate_id}/confirm",
            headers={**auth, "Idempotency-Key": f"memory-{suffix}"},
        )
        confirmed.raise_for_status()

        rag_output = await RagRetrieveHandler(AsyncSessionFactory, embedding)(
            RagRetrieveInput(
                query=atom_text,
                goal_type=GoalType.AGENT_APP,
                limit=3,
            ),
            ToolContext(
                run_id=UUID(replan_run_id),
                user_id=user_id,
                goal_type=GoalType.AGENT_APP,
                intent=RunIntent.REPLAN,
                remaining_deadline_ms=30_000,
            ),
        )
        rag_json = rag_output.model_dump(mode="json")
        rag_items = rag_json.get("items")
        matching = (
            [item for item in rag_items if item.get("atom_id") == str(atom_id)]
            if isinstance(rag_items, list)
            else []
        )
        if not matching:
            raise RuntimeError("rag_retrieve did not return the seeded experience atom")

    return {
        "provider": settings.llm_provider,
        "llm_model": (
            settings.llm_model
            if settings.llm_provider == "openai_compatible"
            else "mock-career-planner-v1"
        ),
        "embedding_provider": settings.embedding_provider,
        "embedding_model": (
            settings.embedding_model_name
            if settings.embedding_provider == "local"
            else "mock-embedding-v1"
        ),
        "embedding_dimension": len(memory_vector),
        "create_plan": {
            "run_id": create_run_id,
            "status": create_result["status"],
            "latency_ms": create_result["total_latency_ms"],
            "tokens_in": create_result["total_tokens_in"],
            "tokens_out": create_result["total_tokens_out"],
        },
        "task": {"state": completed.json()["task"]["state"]},
        "review": {
            "next_plan_action": review_body["next_plan_action"],
            "suggested_replan": review_body["suggested_replan"],
        },
        "replan": {
            "run_id": replan_run_id,
            "status": replan_result["status"],
            "latency_ms": replan_result["total_latency_ms"],
            "tokens_in": replan_result["total_tokens_in"],
            "tokens_out": replan_result["total_tokens_out"],
        },
        "memory_confirmed": True,
        "rag": {
            "returned": len(rag_items) if isinstance(rag_items, list) else 0,
            "seeded_atom_found": True,
            "seeded_atom_similarity": matching[0]["score"],
        },
        "total_wall_latency_ms": int((monotonic() - started) * 1000),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    arguments = parser.parse_args()
    print(json.dumps(asyncio.run(run_demo(arguments.base_url)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
