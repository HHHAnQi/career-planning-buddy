"""Experiment-level runtime context for TrialRunner provider selection.

PR-9c.2 Stage B-1a-lite (Commit 3.5).

The ``ExperimentRuntimeContext`` is a frozen dataclass built ONCE from
the ``EvalExperiment`` row by ``ExperimentRunner.run_experiment`` before
constructing a ``TrialRunner``. It carries exactly the experiment-level
fields that ``TrialRunner._build_executor`` needs to select the correct
planning provider — nothing more.

Design constraints (reviewer-approved P1-lite):

* NO ORM reference: the dataclass is a plain snapshot. ``TrialRunner``
  never touches the DB to read experiment metadata.
* NO mutation: ``frozen=True``.
* ``agent_variant=None`` means legacy path (MockPlanningProvider /
  PairSmokePlanningProvider-via-Settings), so every existing caller that
  does NOT construct a context keeps its current behavior.
* NO dependency on ``app.providers`` — this module lives in
  ``evals/v2`` and is imported upward by ``TrialRunner`` only.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ExperimentRuntimeContext:
    """Frozen snapshot of experiment-level fields consumed by
    ``TrialRunner`` to select deterministic providers.

    Built once from the ``EvalExperiment`` ORM row by
    ``ExperimentRunner.run_experiment``.
    """

    experiment_id: UUID
    agent_variant: str | None
    graph_version: str
    prompt_version: str
    model_version: str
