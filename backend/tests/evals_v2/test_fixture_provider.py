"""PR-5 FixtureProvider unit tests (pure Python, no DB).

Covers the in-memory ``FixtureStore`` interface: lazy record path, idempotent
replay, ``FixtureDesyncError`` on every kind of contract violation. These
complement ``test_fixture_replay_determinism`` (which exercises the DB-backed
record + replay path) by pinning the contract layer's behaviour in isolation.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.harness.provider_calls.fixture_store import (
    MAX_FIXTURE_PAYLOAD_BYTES,
    FixtureDesyncError,
    FixtureEntry,
    FixturePayloadPolicyError,
    FixtureStore,
)


def _entry(
    *,
    sequence: int,
    provider_kind: str = "llm",
    provider_method: str = "generate_agent_turn",
    retry_attempt: int = 0,
    request_projection: dict[str, object] | None = None,
    response_projection: dict[str, object] | None = None,
) -> tuple[FixtureEntry, list[FixtureEntry]]:
    request = request_projection or {"key": f"req-{sequence}"}
    response = response_projection or {"key": f"resp-{sequence}"}
    store = FixtureStore(trial_id=uuid4())
    recorded = store.record(
        sequence=sequence,
        provider_kind=provider_kind,
        provider_method=provider_method,
        retry_attempt=retry_attempt,
        request_projection=request,
        response_projection=response,
        response_payload=response,
    )
    freeze_entries = list(store.entries_by_sequence.values())
    return recorded, freeze_entries


def test_record_then_replay_returns_same_entry() -> None:
    recorded, entries = _entry(sequence=0)
    trial_id = uuid4()

    store = FixtureStore(trial_id=trial_id)
    store.freeze_for_replay(entries)

    consumed = store.consume(
        sequence=0,
        provider_kind="llm",
        provider_method="generate_agent_turn",
        retry_attempt=0,
        request_projection={"key": "req-0"},
    )
    assert consumed.response_projection == {"key": "resp-0"}
    assert consumed.fixture_hash == recorded.fixture_hash
    store.assert_fully_consumed()


def test_replay_rejects_duplicate_consume() -> None:
    _, entries = _entry(sequence=0)
    store = FixtureStore(trial_id=uuid4())
    store.freeze_for_replay(entries)
    store.consume(
        sequence=0,
        provider_kind="llm",
        provider_method="generate_agent_turn",
        retry_attempt=0,
        request_projection={"key": "req-0"},
    )
    with pytest.raises(FixtureDesyncError, match="already consumed"):
        store.consume(
            sequence=0,
            provider_kind="llm",
            provider_method="generate_agent_turn",
            retry_attempt=0,
            request_projection={"key": "req-0"},
        )


def test_replay_rejects_unconsumed_trailing_fixture() -> None:
    _, first = _entry(sequence=0)
    _, second = _entry(sequence=1)
    store = FixtureStore(trial_id=uuid4())
    store.freeze_for_replay([*first, *second])
    store.consume(
        sequence=0,
        provider_kind="llm",
        provider_method="generate_agent_turn",
        retry_attempt=0,
        request_projection={"key": "req-0"},
    )
    with pytest.raises(FixtureDesyncError, match="unconsumed fixture"):
        store.assert_fully_consumed()


def test_replay_missing_sequence_raises_desync() -> None:
    _, entries = _entry(sequence=0)

    store = FixtureStore(trial_id=uuid4())
    store.freeze_for_replay(entries)

    with pytest.raises(FixtureDesyncError, match="sequence not present"):
        store.consume(
            sequence=99,
            provider_kind="llm",
            provider_method="generate_agent_turn",
            retry_attempt=0,
            request_projection={"key": "x"},
        )


def test_replay_request_projection_hash_mismatch_raises_desync() -> None:
    recorded, entries = _entry(sequence=0, request_projection={"expected": 1})

    store = FixtureStore(trial_id=uuid4())
    store.freeze_for_replay(entries)

    with pytest.raises(FixtureDesyncError, match="request_projection_hash"):
        store.consume(
            sequence=0,
            provider_kind="llm",
            provider_method="generate_agent_turn",
            retry_attempt=0,
            request_projection={"different": 2},
        )


def test_replay_provider_kind_mismatch_raises_desync() -> None:
    _, entries = _entry(sequence=0, provider_kind="llm")

    store = FixtureStore(trial_id=uuid4())
    store.freeze_for_replay(entries)

    with pytest.raises(FixtureDesyncError, match="provider_kind"):
        store.consume(
            sequence=0,
            provider_kind="search",
            provider_method="generate_agent_turn",
            retry_attempt=0,
            request_projection={"key": "req-0"},
        )


def test_replay_provider_method_mismatch_raises_desync() -> None:
    _, entries = _entry(sequence=0, provider_method="generate_agent_turn")

    store = FixtureStore(trial_id=uuid4())
    store.freeze_for_replay(entries)

    with pytest.raises(FixtureDesyncError, match="provider_method"):
        store.consume(
            sequence=0,
            provider_kind="llm",
            provider_method="generate_plan",
            retry_attempt=0,
            request_projection={"key": "req-0"},
        )


def test_replay_retry_attempt_mismatch_raises_desync() -> None:
    _, entries = _entry(sequence=0, retry_attempt=1)

    store = FixtureStore(trial_id=uuid4())
    store.freeze_for_replay(entries)

    with pytest.raises(FixtureDesyncError, match="retry_attempt"):
        store.consume(
            sequence=0,
            provider_kind="llm",
            provider_method="generate_agent_turn",
            retry_attempt=0,
            request_projection={"key": "req-0"},
        )


def test_finalize_bundle_hash_stable_for_same_entries() -> None:
    # Two stores, same records added in the same order → identical bundle hash.
    trial_id = uuid4()
    s1 = FixtureStore(trial_id=trial_id)
    s2 = FixtureStore(trial_id=trial_id)
    for seq in range(5):
        for store in (s1, s2):
            store.record(
                sequence=seq,
                provider_kind="llm",
                provider_method="generate_agent_turn",
                retry_attempt=0,
                request_projection={"seq": seq},
                response_projection={"ok": True, "seq": seq},
                response_payload={"ok": True, "seq": seq},
            )
    hash1, count1 = s1.finalize_bundle_hash()
    hash2, count2 = s2.finalize_bundle_hash()
    assert hash1 == hash2
    assert count1 == count2 == 5


def test_freeze_recomputes_response_and_fixture_hashes() -> None:
    recorded, _ = _entry(sequence=0)
    tampered = FixtureEntry(
        sequence=recorded.sequence,
        provider_kind=recorded.provider_kind,
        provider_method=recorded.provider_method,
        retry_attempt=recorded.retry_attempt,
        request_projection_hash=recorded.request_projection_hash,
        response_projection={"tampered": True},
        response_payload=recorded.response_payload,
        response_projection_hash=recorded.response_projection_hash,
        fixture_hash=recorded.fixture_hash,
    )
    with pytest.raises(FixtureDesyncError, match="response_projection_hash"):
        FixtureStore(trial_id=uuid4()).freeze_for_replay([tampered])


def test_record_redacts_secret_keys_without_redacting_usage_counters() -> None:
    store = FixtureStore(trial_id=uuid4())
    entry = store.record(
        sequence=0,
        provider_kind="llm",
        provider_method="generate_agent_turn",
        retry_attempt=0,
        request_projection={"key": "request"},
        response_projection={"ok": True},
        response_payload={
            "authorization": "Bearer secret",
            "usage": {"tokens_in": 4, "tokens_out": 8},
        },
    )
    assert entry.response_payload == {
        "authorization": "[REDACTED]",
        "usage": {"tokens_in": 4, "tokens_out": 8},
    }


def test_record_rejects_oversized_payload() -> None:
    store = FixtureStore(trial_id=uuid4())
    with pytest.raises(FixturePayloadPolicyError, match="exceeds"):
        store.record(
            sequence=0,
            provider_kind="llm",
            provider_method="generate_agent_turn",
            retry_attempt=0,
            request_projection={"key": "request"},
            response_projection={"ok": True},
            response_payload={"output": "x" * MAX_FIXTURE_PAYLOAD_BYTES},
        )


def test_record_after_freeze_raises() -> None:
    _, entries = _entry(sequence=0)

    store = FixtureStore(trial_id=uuid4())
    store.freeze_for_replay(entries)

    with pytest.raises(FixtureDesyncError, match="attempted record after freeze"):
        store.record(
            sequence=1,
            provider_kind="llm",
            provider_method="generate_agent_turn",
            retry_attempt=0,
            request_projection={"key": "x"},
            response_projection={"ok": True},
            response_payload={"ok": True},
        )


def test_freeze_twice_raises() -> None:
    _, entries = _entry(sequence=0)

    store = FixtureStore(trial_id=uuid4())
    store.freeze_for_replay(entries)

    with pytest.raises(RuntimeError, match="called twice"):
        store.freeze_for_replay(entries)


# Type alias guard: keep ``UUID`` in the import set for downstream IDE use.
_UUID_GUARD: type[UUID] = UUID  # noqa: F841
