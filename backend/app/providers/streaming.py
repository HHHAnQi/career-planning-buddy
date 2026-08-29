"""Per-run stream-delta plumbing without provider signature changes.

The graph binds one async sink per LLM call (see
``app.harness.stream_progress.StreamProgressPublisher``); the provider and
client layers read it from this ContextVar so the ``PlanningProvider``
protocol, its four implementations, and the eval replay paths stay
untouched. When no sink is bound (eval harness, direct CLI calls) the
provider still streams at the wire level but forwards deltas to nobody.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

StreamDeltaSink = Callable[[str], Awaitable[None]]

_stream_delta_sink: ContextVar[StreamDeltaSink | None] = ContextVar(
    "career_buddy_stream_delta_sink", default=None
)


def current_stream_delta_sink() -> StreamDeltaSink | None:
    return _stream_delta_sink.get()


@contextmanager
def bind_stream_delta_sink(sink: StreamDeltaSink | None) -> Iterator[None]:
    """Bind ``sink`` for the duration of one LLM call (or None to clear)."""

    token: Token[StreamDeltaSink | None] = _stream_delta_sink.set(sink)
    try:
        yield
    finally:
        _stream_delta_sink.reset(token)
