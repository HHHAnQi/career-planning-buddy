"""Stable internal errors normalized by the Agent Run executor."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


class AgentError(Exception):
    code = "AGENT_ERROR"

    def __init__(
        self,
        message: str = "",
        *,
        retryable: bool | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class RunCancelledError(AgentError):
    code = "RUN_CANCELLED"


class AgentDeadlineExceededError(AgentError):
    code = "AGENT_DEADLINE_EXCEEDED"


class AgentLeaseLostError(AgentError):
    """The current execution attempt no longer owns the durable Run lease."""

    code = "AGENT_LEASE_LOST"


class BudgetExceededError(AgentError):
    code = "AGENT_BUDGET_EXCEEDED"


class ProviderTimeoutError(AgentError):
    code = "PROVIDER_TIMEOUT"


class ProviderConfigurationError(AgentError):
    code = "PROVIDER_CONFIGURATION_INVALID"


class ProviderAuthenticationError(AgentError):
    code = "PROVIDER_AUTHENTICATION_FAILED"


class ProviderRateLimitError(AgentError):
    code = "PROVIDER_RATE_LIMITED"


class ProviderUnavailableError(AgentError):
    code = "PROVIDER_UNAVAILABLE"


class ProviderRetriesExhaustedError(AgentError):
    code = "PROVIDER_RETRIES_EXHAUSTED"


class StructuredOutputError(AgentError):
    code = "STRUCTURED_OUTPUT_INVALID"


class ToolValidationError(AgentError):
    code = "TOOL_ARGUMENT_INVALID"


class ToolExecutionError(AgentError):
    code = "TOOL_EXECUTION_FAILED"


class PersistTransactionError(AgentError):
    code = "PERSIST_TRANSACTION_FAILED"


def parse_retry_after(value: str | None) -> float | None:
    """Parse Retry-After seconds or HTTP-date without exposing response data."""

    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            seconds = (retry_at - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, seconds)
