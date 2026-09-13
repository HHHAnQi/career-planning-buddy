"""PR-9c.1 Pairwise Judge tests (pure Python, no PG, no live LLM).

Pins:
* strict ``PairwiseJudgeOutput`` schema (rejects invalid winner / missing
  dimension / extra field / out-of-range verdict)
* ``normalize_verdict`` flips a↔b under SWAPPED, leaves tie/both_unacceptable
* ``normalize_dimensions`` applies per-dimension
* ``FixturePairwiseJudge`` fail-closed behavior (unmapped pair → invalid)
* ``FixturePairwiseJudge`` mapping → completed result with raw + normalized
* ``OpenAICompatiblePairwiseJudge`` happy path via injected httpx transport
* repair path: malformed first response, valid second response → completed
* repair exhaust: malformed both times → invalid_structured_output
* error mapping: timeout / 5xx / 429 / 401
* ``build_prompt`` shape (system + user, positional dispatching)
* prompt versions frozen

The project runs under ``asyncio_mode = "auto"``, so tests can be ``async def``
directly — no manual event-loop plumbing.
"""

from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.agent.errors import (
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.prompts.pairwise_judge import (
    JUDGE_PROMPT_VERSION,
    JUDGE_RUBRIC_VERSION,
    build_prompt,
)
from evals.v2.judge import (
    DIMENSION_NAMES,
    DimensionVerdict,
    FixturePairwiseJudge,
    JudgeUsage,
    OpenAICompatiblePairwiseJudge,
    PairwiseJudgeOutput,
    PairwiseJudgeResult,
    normalize_dimensions,
    normalize_verdict,
)
from evals.v2.pairwise import (
    Pair,
    PairwiseJudgeInput,
    PositionVariant,
    TrialEvidenceProjection,
)

# ---------------------------------------------------------------- helpers


def _pair() -> Pair:
    b = uuid4()
    c = uuid4()
    return Pair(
        baseline_trial_id=b,
        candidate_trial_id=c,
        case_id="case-1",
        baseline_projection=TrialEvidenceProjection(
            request_constraints={"expect_constraint": "求职"},
            plan_projection={"summary": "b"},
        ),
        candidate_projection=TrialEvidenceProjection(
            request_constraints={"expect_constraint": "求职"},
            plan_projection={"summary": "c"},
        ),
    )


def _judge_input(position: PositionVariant = PositionVariant.BASELINE) -> PairwiseJudgeInput:
    pair = _pair()
    return PairwiseJudgeInput(
        pair=pair,
        judge_run_id=uuid4(),
        position_variant=position,
        rubric=[{"criterion_id": "c1", "description": "有可执行步骤"}],
        display_a=pair.baseline_projection.as_display(),
        display_b=pair.candidate_projection.as_display(),
        input_hash="0" * 64,
    )


def _input_for_pair(
    pair: Pair, position: PositionVariant = PositionVariant.BASELINE,
) -> PairwiseJudgeInput:
    return PairwiseJudgeInput(
        pair=pair,
        judge_run_id=uuid4(),
        position_variant=position,
        rubric=[],
        display_a=pair.baseline_projection.as_display(),
        display_b=pair.candidate_projection.as_display(),
        input_hash="0" * 64,
    )


def _valid_output(*, winner: str = "a") -> PairwiseJudgeOutput:
    return PairwiseJudgeOutput(
        dimension_verdicts={name: "a" for name in DIMENSION_NAMES},
        winner=winner,
        confidence="high",
        rationale="baseline wins on all dimensions",
    )


# ---------------------------------------------------------- schema tests


def test_winner_rejects_invalid_value() -> None:
    with pytest.raises(ValidationError):
        PairwiseJudgeOutput(
            dimension_verdicts={n: "a" for n in DIMENSION_NAMES},
            winner="invalid",
            confidence="high",
            rationale="x",
        )


def test_dimension_verdict_rejects_invalid_value() -> None:
    bad = {n: "a" for n in DIMENSION_NAMES}
    bad["clarity"] = "c"
    with pytest.raises(ValidationError):
        PairwiseJudgeOutput(
            dimension_verdicts=bad,
            winner="a",
            confidence="high",
            rationale="x",
        )


def test_confidence_rejects_invalid_value() -> None:
    with pytest.raises(ValidationError):
        PairwiseJudgeOutput(
            dimension_verdicts={n: "a" for n in DIMENSION_NAMES},
            winner="a",
            confidence="extreme",
            rationale="x",
        )


def test_extra_field_rejected() -> None:
    payload = {
        "dimension_verdicts": {n: "a" for n in DIMENSION_NAMES},
        "winner": "a",
        "confidence": "high",
        "rationale": "x",
        "leaked_role": "baseline",
    }
    with pytest.raises(ValidationError):
        PairwiseJudgeOutput.model_validate(payload)


def test_missing_dimension_rejected() -> None:
    bad = {n: "a" for n in DIMENSION_NAMES}
    del bad["clarity"]
    with pytest.raises(ValidationError):
        PairwiseJudgeOutput(
            dimension_verdicts=bad, winner="a", confidence="high", rationale="x",
        )


def test_empty_rationale_rejected() -> None:
    with pytest.raises(ValidationError):
        PairwiseJudgeOutput(
            dimension_verdicts={n: "a" for n in DIMENSION_NAMES},
            winner="a", confidence="high", rationale="   ",
        )


def test_all_winner_values_accepted() -> None:
    for winner in ("a", "b", "tie", "both_unacceptable"):
        out = PairwiseJudgeOutput(
            dimension_verdicts={n: "tie" for n in DIMENSION_NAMES},
            winner=winner, confidence="low", rationale="x",
        )
        assert out.winner == winner


# ------------------------------------------------------- normalize tests


def test_normalize_verdict_baseline_unchanged() -> None:
    assert normalize_verdict("a", PositionVariant.BASELINE) == "a"
    assert normalize_verdict("b", PositionVariant.BASELINE) == "b"
    assert normalize_verdict("tie", PositionVariant.BASELINE) == "tie"
    assert normalize_verdict("both_unacceptable", PositionVariant.BASELINE) == "both_unacceptable"


def test_normalize_verdict_swapped_flips_a_b() -> None:
    assert normalize_verdict("a", PositionVariant.SWAPPED) == "b"
    assert normalize_verdict("b", PositionVariant.SWAPPED) == "a"
    assert normalize_verdict("tie", PositionVariant.SWAPPED) == "tie"
    assert normalize_verdict("both_unacceptable", PositionVariant.SWAPPED) == "both_unacceptable"


def test_normalize_dimensions_applies_per_dimension() -> None:
    raw: dict[str, DimensionVerdict] = {
        name: ("a" if i % 2 == 0 else "b")
        for i, name in enumerate(DIMENSION_NAMES)
    }
    out = normalize_dimensions(raw, PositionVariant.SWAPPED)
    for i, name in enumerate(DIMENSION_NAMES):
        if i % 2 == 0:
            assert out[name] == "b"
        else:
            assert out[name] == "a"


# ------------------------------------------------------- fixture judge


async def test_fixture_mapping_emits_completed_result() -> None:
    pair = _pair()
    output = _valid_output(winner="a")
    judge = FixturePairwiseJudge(mapping={pair.pair_hash(): output})
    result = await judge.judge(_input_for_pair(pair))
    assert result.judge_run_status == "completed"
    assert result.raw_display_winner == "a"
    assert result.normalized_winner == "a"  # BASELINE → unchanged
    assert result.model_id == "fixture-judge-v1"
    assert result.normalized_dimension_verdicts is not None
    assert set(result.normalized_dimension_verdicts) == set(DIMENSION_NAMES)


async def test_fixture_mapping_swaps_correctly() -> None:
    pair = _pair()
    output = _valid_output(winner="a")
    judge = FixturePairwiseJudge(mapping={pair.pair_hash(): output})
    result = await judge.judge(_input_for_pair(pair, PositionVariant.SWAPPED))
    # SWAPPED: raw "a" (baseline shown as B) → normalized "b"
    assert result.raw_display_winner == "a"
    assert result.normalized_winner == "b"


async def test_fixture_fail_closed_on_unmapped_pair() -> None:
    pair = _pair()
    judge = FixturePairwiseJudge(mapping={})
    result = await judge.judge(_input_for_pair(pair))
    assert result.judge_run_status == "invalid_structured_output"
    assert result.raw_display_winner is None
    assert result.normalized_winner is None


async def test_fixture_dimensions_normalize_independently_under_swap() -> None:
    pair = _pair()
    raw_dims = {n: ("a" if i == 0 else "b") for i, n in enumerate(DIMENSION_NAMES)}
    output = PairwiseJudgeOutput(
        dimension_verdicts=raw_dims,
        winner="a", confidence="low", rationale="x",
    )
    judge = FixturePairwiseJudge(mapping={pair.pair_hash(): output})
    result = await judge.judge(_input_for_pair(pair, PositionVariant.SWAPPED))
    normalized = result.normalized_dimension_verdicts
    assert normalized is not None
    assert normalized[DIMENSION_NAMES[0]] == "b"
    assert normalized[DIMENSION_NAMES[1]] == "a"


# ------------------------------------------------------- prompt tests


def test_prompt_versions_frozen() -> None:
    assert JUDGE_PROMPT_VERSION == "v1"
    assert JUDGE_RUBRIC_VERSION == "v1"


def test_build_prompt_shape() -> None:
    messages = build_prompt(
        context={"request": {"expect_constraint": "求职"}, "rubric": []},
        display_a={"plan": {"summary": "a"}},
        display_b={"plan": {"summary": "b"}},
    )
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "display_a" in messages[1]["content"]
    assert "display_b" in messages[1]["content"]
    assert "求职" in messages[1]["content"]  # ensure_ascii=False


# ------------------------------------------------------- OpenAI adapter


def _mock_transport(responses: list[httpx.Response]) -> httpx.MockTransport:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if not queue:
            raise AssertionError("unexpected extra request")
        return queue.pop(0)

    return httpx.MockTransport(handler)


def _chat_response(body: dict[str, object], status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "choices": [
                {"message": {"role": "assistant", "content": json.dumps(body, ensure_ascii=False)}}
            ]
        },
    )


async def test_openai_happy_path() -> None:
    body: dict[str, object] = {
        "dimension_verdicts": {n: "a" for n in DIMENSION_NAMES},
        "winner": "a",
        "confidence": "high",
        "rationale": "baseline is more concrete",
    }
    transport = _mock_transport([_chat_response(body)])
    judge = OpenAICompatiblePairwiseJudge(
        api_key="k", base_url="https://example.com", model="judge-1",
        transport=transport,
    )
    result = await judge.judge(_judge_input())
    assert result.judge_run_status == "completed"
    assert result.raw_display_winner == "a"
    assert result.normalized_winner == "a"
    assert result.model_id == "judge-1"
    assert isinstance(result.usage, JudgeUsage)


async def test_openai_repair_then_success() -> None:
    bad_response = _chat_response({"malformed": True})
    good_body: dict[str, object] = {
        "dimension_verdicts": {n: "tie" for n in DIMENSION_NAMES},
        "winner": "tie",
        "confidence": "medium",
        "rationale": "难分高下",
    }
    good_response = _chat_response(good_body)
    transport = _mock_transport([bad_response, good_response])
    judge = OpenAICompatiblePairwiseJudge(
        api_key="k", base_url="https://example.com", model="judge-1",
        transport=transport,
    )
    result = await judge.judge(_judge_input())
    assert result.judge_run_status == "completed"
    assert result.raw_display_winner == "tie"


async def test_openai_repair_exhausts_to_invalid() -> None:
    bad_a = _chat_response({"malformed": True})
    bad_b = _chat_response({"malformed": True})
    transport = _mock_transport([bad_a, bad_b])
    judge = OpenAICompatiblePairwiseJudge(
        api_key="k", base_url="https://example.com", model="judge-1",
        transport=transport,
    )
    result = await judge.judge(_judge_input())
    assert result.judge_run_status == "invalid_structured_output"
    assert result.normalized_winner is None


async def test_openai_timeout_maps_to_provider_timeout() -> None:
    def raise_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    judge = OpenAICompatiblePairwiseJudge(
        api_key="k", base_url="https://example.com", model="judge-1",
        transport=httpx.MockTransport(raise_timeout),
    )
    with pytest.raises(ProviderTimeoutError):
        await judge.judge(_judge_input())


async def test_openai_429_maps_to_rate_limit() -> None:
    judge = OpenAICompatiblePairwiseJudge(
        api_key="k", base_url="https://example.com", model="judge-1",
        transport=httpx.MockTransport(lambda r: httpx.Response(429)),
    )
    with pytest.raises(ProviderRateLimitError):
        await judge.judge(_judge_input())


async def test_openai_401_maps_to_auth_error() -> None:
    judge = OpenAICompatiblePairwiseJudge(
        api_key="k", base_url="https://example.com", model="judge-1",
        transport=httpx.MockTransport(lambda r: httpx.Response(401)),
    )
    with pytest.raises(ProviderAuthenticationError):
        await judge.judge(_judge_input())


async def test_openai_5xx_maps_to_unavailable() -> None:
    judge = OpenAICompatiblePairwiseJudge(
        api_key="k", base_url="https://example.com", model="judge-1",
        transport=httpx.MockTransport(lambda r: httpx.Response(503)),
    )
    with pytest.raises(ProviderUnavailableError):
        await judge.judge(_judge_input())


async def test_result_carries_metadata_for_position_audit() -> None:
    """A completed result exposes both raw + normalized winners so callers
    can compute position consistency across paired runs."""

    pair = _pair()
    output = _valid_output(winner="a")
    judge = FixturePairwiseJudge(mapping={pair.pair_hash(): output})
    prompt = PairwiseJudgeInput(
        pair=pair, judge_run_id=uuid4(),
        position_variant=PositionVariant.BASELINE,
        rubric=[],
        display_a=pair.baseline_projection.as_display(),
        display_b=pair.candidate_projection.as_display(),
        input_hash="abc",
    )
    result = await judge.judge(prompt)
    assert isinstance(result, PairwiseJudgeResult)
    assert result.raw_display_winner == "a"
    assert result.normalized_winner == "a"
    assert result.prompt_config.prompt_version == "v1"
    assert result.prompt_config.rubric_version == "v1"
    assert result.input_hash == "abc"
