"""LangGraph fan-out topology assertions for the context loader split.

Proves, on the REAL compiled graph of FixedPlanningGraph:
1. Topology — intent_router fans out to memory_loader AND evidence_loader,
   both joining at context_builder (native parallel superstep edges).
2. Execution — the two loader nodes actually run CONCURRENTLY: their
   instrumented execution intervals overlap in wall-clock time.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agent.graph import FixedPlanningGraph
from app.schemas.enums import RunIntent


def _interval_node(name: str, events: list[tuple[str, float, float]]):
    async def run(_state):
        start = time.monotonic()
        await asyncio.sleep(0.05)
        events.append((name, start, time.monotonic()))
        return {}

    return run


def _stub_graph() -> tuple[FixedPlanningGraph, list[tuple[str, float, float]]]:
    events: list[tuple[str, float, float]] = []
    graph = object.__new__(FixedPlanningGraph)
    graph._context_db_lock = asyncio.Lock()

    async def risk(_state):
        return {"risk": SimpleNamespace(level="safe")}

    async def intent(_state):
        return {
            "intent": SimpleNamespace(
                intent=RunIntent.CREATE_PLAN,
                missing_slots=[],
            )
        }

    async def validator(_state):
        return {"validation_report": SimpleNamespace(passed=True)}

    graph._risk_node = risk
    graph._safe_response_node = _noop
    graph._intent_node = intent
    graph._navigation_node = _noop
    graph._clarification_node = _noop
    graph._memory_loader_node = _interval_node("memory_loader", events)
    graph._evidence_loader_node = _interval_node("evidence_loader", events)
    graph._context_node = _noop
    graph._agent_node = _noop
    graph._validator_node = validator
    graph._revise_node = _noop
    graph._companion_node = _noop
    graph._persist_node = _noop
    return graph, events


async def _noop(_state):
    return {}


def _overlaps(
    first: tuple[str, float, float], second: tuple[str, float, float]
) -> float:
    return min(first[2], second[2]) - max(first[1], second[1])


@pytest.mark.asyncio
async def test_fan_out_topology_connects_both_loaders_to_the_join_node() -> None:
    graph, _ = _stub_graph()
    compiled = graph._build_graph()
    # Static join edges are declared on the builder (the drawable graph
    # prunes conditional-edge targets it cannot statically expand).
    edges = {(str(source), str(target)) for source, target in compiled.builder.edges}
    assert ("memory_loader", "context_builder") in edges
    assert ("evidence_loader", "context_builder") in edges
    # The conditional fan-out is a routing function (LangGraph expands it
    # at runtime), so assert it dispatches to BOTH loader nodes at once.
    ready_state = {
        "intent": SimpleNamespace(
            intent=RunIntent.CREATE_PLAN,
            missing_slots=[],
        )
    }
    assert set(FixedPlanningGraph._route_after_intent(ready_state)) == {
        "memory_loader",
        "evidence_loader",
    }


@pytest.mark.asyncio
async def test_loader_nodes_execute_concurrently_in_one_superstep() -> None:
    graph, events = _stub_graph()
    compiled = graph._build_graph()
    await compiled.ainvoke({"run_id": uuid4()})

    by_name = {name: (name, start, end) for name, start, end in events}
    assert set(by_name) == {"memory_loader", "evidence_loader"}
    overlap = _overlaps(by_name["memory_loader"], by_name["evidence_loader"])
    # Each stub sleeps 50ms; serial execution would give overlap <= 0.
    # Concurrent superstep execution overlaps by nearly the full sleep.
    assert overlap > 0.03, (
        f"loaders did not run concurrently (overlap={overlap:.3f}s): {events}"
    )
