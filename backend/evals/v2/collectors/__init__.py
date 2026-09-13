"""Outcome + evidence collectors for the V2 TrialRunner.

These are thin read-only projections over the persisted Runtime trace. They
never mutate Run/Plan/Step/Event state and contain no Runtime logic.

PR-3 ships ``outcome``; PR-4 completes the layout with ``evidence``.
"""

from .evidence import collect_evidence
from .outcome import (
    RunOutcome,
    collect_outcome,
    terminal_event_count,
    terminal_events,
)

__all__ = [
    "RunOutcome",
    "collect_evidence",
    "collect_outcome",
    "terminal_event_count",
    "terminal_events",
]
