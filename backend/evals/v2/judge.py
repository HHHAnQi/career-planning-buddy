"""PR-9c.1 Pairwise Judge protocol, Fixture mock, and OpenAI-compatible adapter.

This module owns the contract every Pairwise Judge honours:

* a ``PairwiseJudge.judge`` coroutine that takes a frozen
  ``PairwiseJudgeInput`` and returns a ``PairwiseJudgeOutput`` (or raises
  one of the normalized provider errors);
* a strict output schema (``PairwiseJudgeOutput``) so an LLM that emits a
  malformed body fails closed — the caller records
  ``judge_run_status='invalid_structured_output'`` and never silently
  fabricates a verdict;
* ``normalize_verdict`` / ``normalize_dimensions``: pure functions that
  flip a↔b when the run was ``PositionVariant.SWAPPED``, leaving
  ``tie`` / ``both_unacceptable`` invariant. The Judge itself never knows
  about position; normalization is the caller's responsibility.

Architecture invariants (see plan):

* No import from ``app.harness.*`` / ``app.agent.*``. The Judge lives in
  the Eval Harness layer.
* ``PairwiseJudgeInput.display_a`` / ``display_b`` are the only source of
  trial content the Judge ever sees; baseline/candidate/model/auto-scores
  are absent by construction.
* Invalid structured output is repaired at most once; further failure
  yields ``invalid_structured_output`` status, persisted by commit 2.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from time import monotonic
from typing import Literal, Protocol
from uuid import UUID

import httpx
from pydantic import ValidationError, field_validator, model_validator

from app.agent.errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.prompts.pairwise_judge import (
    JUDGE_PROMPT_VERSION,
    JUDGE_RUBRIC_VERSION,
    build_prompt,
)
from app.schemas.base import StrictModel
from evals.v2.pairwise import PairwiseJudgeInput, PositionVariant

# The four physical verdicts the Judge may return. The user explicitly
# rejected numeric scores and insisted invalidity is NOT a winner value —
# invalid output manifests as ``judge_run_status='invalid_structured_output'``.
WinLabel = Literal["a", "b", "tie", "both_unacceptable"]
DimensionVerdict = Literal["a", "b", "tie", "both_unacceptable"]
Confidence = Literal["low", "medium", "high"]

# The five dimensions (frozen by spec; renaming any invalidates calibration
# under ``JUDGE_RUBRIC_VERSION``). PR-9c.1 emits categorical verdicts only.
DimensionName = Literal[
    "actionability",
    "alignment",
    "personalization",
    "clarity",
    "consistency",
]
DIMENSION_NAMES: tuple[DimensionName, ...] = (
    "actionability",
    "alignment",
    "personalization",
    "clarity",
    "consistency",
)

# Run-level status (one row per physical Judge execution). ``completed`` is
# the only state that carries a usable verdict; ``invalid_structured_output``
# means we could not coerce the LLM body into PairwiseJudgeOutput after the
# allowed repair attempts. Provider errors propagate as their native
# exceptions so the service layer can retry / surface them.
JudgeRunStatus = Literal["completed", "invalid_structured_output"]


class PairwiseJudgeOutput(StrictModel):
    """Strict schema for one Judge LLM response body.

    Any deviation (missing dimension, unknown verdict, extra field) makes
    the body unparseable; the adapter records
    ``invalid_structured_output`` rather than guessing. ``dimension_verdicts``
    must enumerate exactly the five dimensions in ``DIMENSION_NAMES``.
    """

    dimension_verdicts: dict[DimensionName, DimensionVerdict]
    winner: WinLabel
    confidence: Confidence
    rationale: str

    @field_validator("rationale")
    @classmethod
    def _rationale_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("rationale must not be empty")
        return value

    @model_validator(mode="after")
    def _all_dimensions_present(self) -> PairwiseJudgeOutput:
        missing = set(DIMENSION_NAMES) - set(self.dimension_verdicts)
        if missing:
            raise ValueError(
                f"dimension_verdicts missing required dimensions: {sorted(missing)}"
            )
        return self

    def has_all_dimensions(self) -> bool:
        return set(self.dimension_verdicts) == set(DIMENSION_NAMES)


@dataclass(frozen=True, slots=True)
class PairwiseJudgePromptConfig:
    """Frozen prompt versioning carried alongside a Judge result."""

    prompt_version: str = JUDGE_PROMPT_VERSION
    rubric_version: str = JUDGE_RUBRIC_VERSION


@dataclass(frozen=True, slots=True)
class PairwiseJudgePrompt:
    """One rendered chat payload ready to send to an LLM."""

    messages: list[dict[str, str]]
    config: PairwiseJudgePromptConfig


@dataclass(frozen=True, slots=True)
class JudgeUsage:
    """Lightweight usage record for one Judge execution."""

    tokens_in: int
    tokens_out: int
    latency_ms: int
    raw_output_hash: str


@dataclass(frozen=True, slots=True)
class PairwiseJudgeResult:
    """What one physical Judge execution produced.

    ``raw_display_winner`` is the display-side winner (a/b/tie/...).
    ``normalized_winner`` is the baseline-relative winner after applying
    ``normalize_verdict`` with the run's ``position_variant``. Both are
    persisted (commit 2) so downstream analysis can audit position bias
    without re-running the Judge.
    """

    judge_run_id: UUID
    judge_run_status: JudgeRunStatus
    raw_display_winner: WinLabel | None
    normalized_winner: WinLabel | None
    raw_dimension_verdicts: dict[str, DimensionVerdict] | None
    normalized_dimension_verdicts: dict[str, DimensionVerdict] | None
    confidence: Confidence | None
    rationale: str | None
    usage: JudgeUsage | None
    model_id: str
    prompt_config: PairwiseJudgePromptConfig
    input_hash: str


def normalize_verdict(verdict: WinLabel, position_variant: PositionVariant) -> WinLabel:
    """Map a display-side winner to a baseline-relative winner.

    Under ``PositionVariant.SWAPPED`` the baseline was shown as B, so a
    Judge winner of "a" actually means the candidate won; flip a↔b.
    "tie" and "both_unacceptable" are position-invariant. The function is
    total: any verdict survives normalization unchanged except the a/b
    swap.
    """

    if position_variant is PositionVariant.SWAPPED:
        if verdict == "a":
            return "b"
        if verdict == "b":
            return "a"
    return verdict


def normalize_dimensions(
    verdicts: Mapping[str, DimensionVerdict],
    position_variant: PositionVariant,
) -> dict[str, DimensionVerdict]:
    """Apply ``normalize_verdict`` to each dimension verdict independently."""

    return {
        name: normalize_verdict(verdict, position_variant)
        for name, verdict in verdicts.items()
    }


class PairwiseJudge(Protocol):
    """Protocol every Pairwise Judge implementation satisfies."""

    @property
    def model_id(self) -> str: ...

    async def judge(self, prompt: PairwiseJudgeInput) -> PairwiseJudgeResult: ...


class FixturePairwiseJudge:
    """Deterministic, fail-closed mock Pairwise Judge for tests.

    The caller supplies an explicit ``mapping`` from a label (typically the
    Pair's ``pair_hash``) to the ``PairwiseJudgeOutput`` the fixture should
    emit. There is NO pseudorandom fallback from ``pair_hash`` — if a label
    has no mapping, the fixture records ``invalid_structured_output``.
    This makes tests fully declarative: a given Pair always produces the
    same verdict, and an unmapped Pair always fails closed.
    """

    def __init__(
        self,
        *,
        mapping: Mapping[str, PairwiseJudgeOutput],
        model_id: str = "fixture-judge-v1",
    ) -> None:
        self._mapping = dict(mapping)
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        return self._model_id

    async def judge(self, prompt: PairwiseJudgeInput) -> PairwiseJudgeResult:
        key = prompt.pair.pair_hash()
        output = self._mapping.get(key)
        position_variant = prompt.position_variant
        if output is None:
            return PairwiseJudgeResult(
                judge_run_id=prompt.judge_run_id,
                judge_run_status="invalid_structured_output",
                raw_display_winner=None,
                normalized_winner=None,
                raw_dimension_verdicts=None,
                normalized_dimension_verdicts=None,
                confidence=None,
                rationale=None,
                usage=None,
                model_id=self._model_id,
                prompt_config=PairwiseJudgePromptConfig(),
                input_hash=prompt.input_hash,
            )

        raw_dims: dict[str, DimensionVerdict] = {
            str(name): verdict for name, verdict in output.dimension_verdicts.items()
        }
        return PairwiseJudgeResult(
            judge_run_id=prompt.judge_run_id,
            judge_run_status="completed",
            raw_display_winner=output.winner,
            normalized_winner=normalize_verdict(output.winner, position_variant),
            raw_dimension_verdicts=raw_dims,
            normalized_dimension_verdicts=normalize_dimensions(raw_dims, position_variant),
            confidence=output.confidence,
            rationale=output.rationale,
            usage=None,
            model_id=self._model_id,
            prompt_config=PairwiseJudgePromptConfig(),
            input_hash=prompt.input_hash,
        )


class OpenAICompatiblePairwiseJudge:
    """OpenAI-compatible Chat Completions adapter for the Pairwise Judge.

    Mirrors ``OpenAICompatiblePlanningProvider``: httpx async client,
    deterministic error mapping (Timeout→ProviderTimeoutError,
    RequestError→ProviderUnavailableError, 401/403→ProviderAuthError,
    429→ProviderRateLimitError), and one ``response_format=json_object``
    call. Output is parsed into ``PairwiseJudgeOutput``; a malformed body
    gets at most one self-repair re-send, after which the result is
    marked ``invalid_structured_output`` (invariant #6).

    The provider never sees ``PositionVariant`` semantics — it builds a
    ``PairwiseJudgePrompt`` and parses the body. Normalization to
    baseline-relative verdicts happens here, in the caller's process,
    once the body is parsed successfully.
    """

    REPAIR_INSTRUCTION = (
        "上一条输出不是合法 JSON verdict 对象。请仅输出符合 schema 的 JSON 对象，"
        "不要任何额外字段、markdown 或解释。"
    )

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 30,
        max_output_tokens: int = 800,
        temperature: float = 0.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key or not base_url or not model:
            raise ProviderConfigurationError(
                "openai_compatible judge requires API key, base URL, and model"
            )
        self._api_key = api_key
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._transport = transport

    @property
    def model_id(self) -> str:
        return self._model

    async def judge(self, prompt: PairwiseJudgeInput) -> PairwiseJudgeResult:
        messages = build_prompt(
            context={
                "request": prompt.display_a.get("request", {}),
                "rubric": prompt.rubric,
            },
            display_a=prompt.display_a,
            display_b=prompt.display_b,
        )
        body_text, latency_ms = await self._send(messages)
        output = self._parse(body_text)

        if output is None:
            # invariant #6: one repair attempt, then fail closed.
            repaired_messages = list(messages) + [
                {"role": "assistant", "content": body_text},
                {"role": "user", "content": self.REPAIR_INSTRUCTION},
            ]
            body_text, latency_ms = await self._send(repaired_messages)
            output = self._parse(body_text)

        return self._build_result(prompt, output, body_text, latency_ms)

    async def _send(self, messages: list[dict[str, str]]) -> tuple[str, int]:
        started = monotonic()
        request_body: dict[str, object] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_output_tokens,
            "response_format": {"type": "json_object"},
        }
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=self._timeout_seconds,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            ) as client:
                response = await client.post(self._endpoint, json=request_body)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Pairwise Judge request timed out") from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailableError("Pairwise Judge provider unreachable") from exc

        if response.status_code in {401, 403}:
            raise ProviderAuthenticationError("Pairwise Judge auth rejected")
        if response.status_code == 429:
            raise ProviderRateLimitError("Pairwise Judge rate limited")
        if response.status_code >= 400:
            raise ProviderUnavailableError(
                f"Pairwise Judge HTTP {response.status_code}"
            )

        latency_ms = int((monotonic() - started) * 1000)
        content = self._extract_content(response)
        if content is None:
            details = self._empty_content_details(response)
            raise ProviderUnavailableError(
                f"Pairwise Judge returned empty content ({details})"
            )
        return content, latency_ms

    @staticmethod
    def _extract_content(response: httpx.Response) -> str | None:
        try:
            body: object = response.json()
        except ValueError:
            return None
        if not isinstance(body, Mapping):
            return None
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        first = choices[0]
        if not isinstance(first, Mapping):
            return None
        message = first.get("message")
        if not isinstance(message, Mapping):
            return None
        content = message.get("content")
        return content if isinstance(content, str) and content.strip() else None

    @staticmethod
    def _empty_content_details(response: httpx.Response) -> str:
        """Return non-content diagnostics without leaking model reasoning."""

        try:
            body: object = response.json()
        except ValueError:
            return "invalid_json=true"
        if not isinstance(body, Mapping):
            return "invalid_body=true"
        choices = body.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        finish_reason = first.get("finish_reason") if isinstance(first, Mapping) else None
        message = first.get("message") if isinstance(first, Mapping) else None
        has_reasoning = bool(
            isinstance(message, Mapping)
            and isinstance(message.get("reasoning_content"), str)
            and message.get("reasoning_content")
        )
        usage = body.get("usage")
        completion_tokens = (
            usage.get("completion_tokens") if isinstance(usage, Mapping) else None
        )
        return (
            f"finish_reason={finish_reason!s},"
            f"has_reasoning={str(has_reasoning).lower()},"
            f"completion_tokens={completion_tokens!s}"
        )

    @staticmethod
    def _parse(body_text: str) -> PairwiseJudgeOutput | None:
        try:
            parsed = json.loads(body_text)
        except ValueError:
            return None
        if not isinstance(parsed, dict):
            return None
        try:
            output = PairwiseJudgeOutput.model_validate(parsed)
        except ValidationError:
            return None
        if not output.has_all_dimensions():
            return None
        return output

    def _build_result(
        self,
        prompt: PairwiseJudgeInput,
        output: PairwiseJudgeOutput | None,
        body_text: str,
        latency_ms: int,
    ) -> PairwiseJudgeResult:
        raw_output_hash = sha256(body_text.encode("utf-8")).hexdigest()
        usage = JudgeUsage(
            tokens_in=0,
            tokens_out=0,
            latency_ms=latency_ms,
            raw_output_hash=raw_output_hash,
        )
        if output is None:
            return PairwiseJudgeResult(
                judge_run_id=prompt.judge_run_id,
                judge_run_status="invalid_structured_output",
                raw_display_winner=None,
                normalized_winner=None,
                raw_dimension_verdicts=None,
                normalized_dimension_verdicts=None,
                confidence=None,
                rationale=None,
                usage=usage,
                model_id=self._model,
                prompt_config=PairwiseJudgePromptConfig(),
                input_hash=prompt.input_hash,
            )

        raw_dims: dict[str, DimensionVerdict] = {
            str(name): verdict for name, verdict in output.dimension_verdicts.items()
        }
        return PairwiseJudgeResult(
            judge_run_id=prompt.judge_run_id,
            judge_run_status="completed",
            raw_display_winner=output.winner,
            normalized_winner=normalize_verdict(output.winner, prompt.position_variant),
            raw_dimension_verdicts=raw_dims,
            normalized_dimension_verdicts=normalize_dimensions(raw_dims, prompt.position_variant),
            confidence=output.confidence,
            rationale=output.rationale,
            usage=usage,
            model_id=self._model,
            prompt_config=PairwiseJudgePromptConfig(),
            input_hash=prompt.input_hash,
        )
