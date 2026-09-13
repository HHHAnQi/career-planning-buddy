"""PR-9b Cluster A: failure taxonomy tests.

Pure-Python (no DB) tests confirming:
- every ``EvalFailureCode`` maps to a ``FailureCategory``;
- bucket helpers (runtime / configuration / cancel) are partitioned correctly;
- the recorder path uses ``exc.code`` rather than ``type(exc).__name__``.
"""

from __future__ import annotations

import pytest

from app.agent.errors import (
    AgentError,
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.harness.errors import (
    CONFIGURATION_FAILURE_CODES,
    RUNTIME_FAILURE_CATEGORIES,
    RUNTIME_FAILURE_CODES,
    USER_CANCEL_CODES,
    EvalFailureCode,
    FailureCategory,
    category,
    is_configuration_failure,
    is_runtime_failure,
    is_user_cancel,
    normalize_failure_code,
)


def test_every_eval_failure_code_has_a_category() -> None:
    """The classifier must cover every enum value with no UNKNOWN fallthrough."""

    missing = [
        code
        for code in EvalFailureCode
        if code is not EvalFailureCode.UNKNOWN
        and category(code.value) == FailureCategory.UNKNOWN
    ]
    assert not missing, f"unclassified codes: {[c.value for c in missing]}"


def test_runtime_failure_codes_match_category_classifier() -> None:
    """Codes in the public RUNTIME_FAILURE_CODES set are exactly those whose
    ``category(code.value) in RUNTIME_FAILURE_CATEGORIES``."""

    for code in EvalFailureCode:
        expected = category(code.value) in RUNTIME_FAILURE_CATEGORIES
        assert is_runtime_failure(code.value) is expected


def test_runtime_failure_excludes_config_and_user_action() -> None:
    """CONFIG / USER_ACTION / QUALITY codes never count as runtime failures."""

    non_runtime = (
        CONFIGURATION_FAILURE_CODES
        | USER_CANCEL_CODES
        | {EvalFailureCode.QUALITY_HARD_GATE_FAILED.value}
    )
    for code in non_runtime:
        assert not is_runtime_failure(code), f"{code} must not be runtime"


def test_configuration_failure_only_carries_config_codes() -> None:
    for code in CONFIGURATION_FAILURE_CODES:
        assert is_configuration_failure(code)
        assert category(code) == FailureCategory.CONFIG


def test_user_cancel_only_carries_user_action_codes() -> None:
    for code in USER_CANCEL_CODES:
        assert is_user_cancel(code)
        assert not is_runtime_failure(code)


def test_normalize_failure_code_round_trips_known_codes() -> None:
    assert normalize_failure_code("PROVIDER_UNAVAILABLE") == (
        EvalFailureCode.PROVIDER_UNAVAILABLE
    )
    assert normalize_failure_code(None) == EvalFailureCode.UNKNOWN
    assert normalize_failure_code("not_a_real_code") == EvalFailureCode.UNKNOWN


@pytest.mark.parametrize(
    ("exc_factory", "expected_code"),
    [
        (ProviderUnavailableError, EvalFailureCode.PROVIDER_UNAVAILABLE.value),
        (ProviderTimeoutError, EvalFailureCode.PROVIDER_TIMEOUT.value),
        (ProviderRateLimitError, EvalFailureCode.PROVIDER_RATE_LIMITED.value),
        (
            ProviderAuthenticationError,
            EvalFailureCode.PROVIDER_AUTHENTICATION_FAILED.value,
        ),
    ],
)
def test_agent_error_subclass_has_taxonomised_code(
    exc_factory: type[AgentError], expected_code: str
) -> None:
    """Agent-runtime errors remain the source of truth for code strings."""

    exc = exc_factory("boom")
    assert exc.code == expected_code
    assert is_runtime_failure(exc.code)
    assert normalize_failure_code(exc.code).value == expected_code


def test_eval_failure_code_str_enum_value_round_trip() -> None:
    """StrEnum values must remain plain strings so SQL/JSON keep working."""

    assert str(EvalFailureCode.PROVIDER_TIMEOUT) == "PROVIDER_TIMEOUT"
    assert EvalFailureCode("PROVIDER_TIMEOUT") is EvalFailureCode.PROVIDER_TIMEOUT
    # Bucket sets expose the same string values, so SQL/JSON consumers see
    # the same literals whether they read the enum or the frozenset.
    assert EvalFailureCode.PROVIDER_TIMEOUT.value in RUNTIME_FAILURE_CODES
