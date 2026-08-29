"""Deadline-bounded terminal wait for one Agent Run.

PR-3 requires isdeadline-independent timeout handling: if a Run does not reach
a terminal status within the TrialRunner's deadline, the Trial must be marked
``timed_out`` and the Run cancelled -- the waiter must NEVER synthesize a
fake terminal.

The waiter wraps ``AgentRunExecutor.execute`` in ``asyncio.wait_for``. On
timeout it requests cancellation through ``AgentRunExecutor.request_cancel``
(which the executor turns into exactly one ``run.cancelled`` terminal) and
reports whether the Run reached a real terminal state.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class WaitOutcome(StrEnum):
    COMPLETED = "completed"  # execute() returned and Run is in a terminal state
    TIMED_OUT = "timed_out"  # deadline hit; cancel requested
    FAILED = "failed"  # execute() raised a non-cancel exception


@dataclass(frozen=True, slots=True)
class WaitResult:
    outcome: WaitOutcome
    terminal_status: str | None  # the AgentRun.status value, if reached


TERMINAL_STATUSES = frozenset({"completed", "degraded", "failed", "cancelled"})


class TerminalWaiter:
    """Drives ``executor.execute`` under a hard TrialRunner deadline."""

    def __init__(self, *, deadline_seconds: float) -> None:
        if deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be positive")
        self._deadline_seconds = deadline_seconds

    async def await_terminal(
        self,
        execute_coro: Awaitable[None],
        *,
        run_id: UUID,
        read_status: Callable[[], Awaitable[str | None]],
        request_cancel: Callable[[], Awaitable[None]],
    ) -> WaitResult:
        """Run ``execute_coro`` under a deadline; cancel on timeout.

        ``read_status`` is an async callable returning the current
        ``AgentRun.status`` string (or ``None``); ``request_cancel`` is an
        async callable that triggers the executor's cooperative cancel path.
        """

        del run_id  # part of the contract; not needed for the Awaitable form
        try:
            await asyncio.wait_for(execute_coro, timeout=self._deadline_seconds)
        except TimeoutError:
            await request_cancel()
            # Drain the executor: ``request_cancel`` cancels the asyncio Task;
            # the executor's CancelledError handler finalizes exactly one
            # ``run.cancelled`` terminal.
            status = await read_status()
            if status in TERMINAL_STATUSES:
                return WaitResult(WaitOutcome.TIMED_OUT, status)
            return WaitResult(WaitOutcome.TIMED_OUT, None)
        except asyncio.CancelledError:
            # Propagated cancellation from a cooperative cancel race
            # (revision #7). This is a legitimate terminal path.
            status = await read_status()
            return WaitResult(WaitOutcome.COMPLETED, status)
        except Exception:
            # execute() swallows its own exceptions and finalizes; an escape
            # means the run is in an indeterminate state.
            status = await read_status()
            if status in TERMINAL_STATUSES:
                return WaitResult(WaitOutcome.COMPLETED, status)
            return WaitResult(WaitOutcome.FAILED, status)
        status = await read_status()
        if status in TERMINAL_STATUSES:
            return WaitResult(WaitOutcome.COMPLETED, status)
        return WaitResult(WaitOutcome.FAILED, status)
